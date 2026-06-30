# HeyReach Stats API Reference

Source: `GET /api/public/analytics/get-overall-stats`  
MCP tool: `get_overall_stats(accountIds, campaignIds, startDate, endDate)`

Verified from live response 2026-06-30.

---

## Filters

| Parameter | Type | Description |
|---|---|---|
| `accountIds` | `int[] \| null` | LinkedIn sender account IDs. null = all accounts |
| `campaignIds` | `int[] \| null` | HeyReach campaign IDs. null = all campaigns |
| `startDate` | `datetime \| null` | Range start (ISO 8601). null = all time |
| `endDate` | `datetime \| null` | Range end (ISO 8601). null = today |

---

## Response Shape

```json
{
  "overallStats": { ...StatsObject },
  "byDayStats": {
    "2026-06-10T00:00:00Z": { ...StatsObject },
    "2026-06-11T00:00:00Z": { ...StatsObject }
  }
}
```

`byDayStats` keys are UTC ISO timestamps (midnight). Each value is a `StatsObject`.

---

## StatsObject Fields

### Activity Counts (integers)

| Field | Description |
|---|---|
| `profileViews` | Profile views triggered by the sequence |
| `postLikes` | Post likes performed |
| `follows` | Follow actions performed |
| `connectionsSent` | Connection requests sent |
| `connectionsAccepted` | Connection requests accepted (can lag sent by days) |
| `messagesSent` | DMs sent to already-connected leads |
| `totalMessageStarted` | Unique leads who entered a message step |
| `totalMessageReplies` | Replies received to DMs |
| `inmailMessagesSent` | InMail messages sent |
| `totalInmailStarted` | Unique leads who entered an InMail step |
| `totalInmailReplies` | Replies received to InMails |
| `uniqueLeadsContacted` | Unique leads contacted (deduped across channels) |
| `autoTaggedInterested` | Leads auto-tagged as "Interested" |
| `totalAutoTagged` | Total leads auto-tagged (all tags) |

### Rate Fields (float, 0.0–1.0)

| Field | Formula | Example |
|---|---|---|
| `connectionAcceptanceRate` | `connectionsAccepted / connectionsSent` | `0.169` = 16.9% |
| `messageReplyRate` | `totalMessageReplies / totalMessageStarted` | `0.263` = 26.3% |
| `inMailReplyRate` | `totalInmailReplies / totalInmailStarted` | `0.0` = 0% |
| `autoTaggedInterestedRate` | `autoTaggedInterested / totalAutoTagged` | `0.6` = 60% |

Rates are `0.0` when denominator is 0 (no division-by-zero errors in API).

---

## Live Baseline (Mythic workspace, all time as of 2026-06-30)

```
connectionsSent:        130
connectionsAccepted:     22   → 16.9% acceptance rate
messagesSent:            25
totalMessageStarted:     19
totalMessageReplies:      5   → 26.3% reply rate
uniqueLeadsContacted:   130
profileViews:            79
postLikes:               68
autoTaggedInterested:     3
totalAutoTagged:          5   → 60% of tagged = interested
inmailMessagesSent:       0   (not used)
```

---

## Dashboard Query Patterns

### Overall campaign health (all time)
```python
get_overall_stats()  # no filters
# → overallStats gives totals
```

### Per-campaign breakdown
```python
# For each campaign_id stored in MongoDB heyreach_campaign_id:
get_overall_stats(campaignIds=[campaign_id])
```

### Date-range trend (last 30 days)
```python
get_overall_stats(
    startDate="2026-06-01T00:00:00Z",
    endDate="2026-06-30T00:00:00Z"
)
# → byDayStats has one entry per day
# → keys are ISO timestamps, values are StatsObject
```

### Per-sender account breakdown
```python
get_overall_stats(accountIds=[account_id])
```

### Campaign + date range (most specific)
```python
get_overall_stats(
    campaignIds=[123, 456],
    startDate="2026-06-01T00:00:00Z",
    endDate="2026-06-30T00:00:00Z"
)
```

---

## Key Dashboard Metrics (recommended)

### Top-level KPIs
- `connectionAcceptanceRate` × 100 → "CR Acceptance %"
- `messageReplyRate` × 100 → "DM Reply Rate %"
- `connectionsAccepted` → total pipeline entries
- `totalMessageReplies` → positive engagement count

### Funnel
```
connectionsSent
  └─ connectionsAccepted  (connectionAcceptanceRate)
       └─ totalMessageStarted
            └─ totalMessageReplies  (messageReplyRate)
                 └─ autoTaggedInterested
```

### Daily trend chart
Loop `byDayStats` sorted by date key. Plot:
- `connectionsSent` (bar)
- `connectionsAccepted` (bar)
- `connectionAcceptanceRate` × 100 (line, right axis)
- `totalMessageReplies` (bar)

### Notes
- `connectionsAccepted` on a day = acceptances that arrived on that day, not necessarily from that day's sends. Expect lag of 3–14 days.
- `uniqueLeadsContacted` ≠ `connectionsSent + messagesSent`. It's deduped across actions.
- InMail fields (`inmailMessagesSent`, `totalInmailStarted`, `totalInmailReplies`, `inMailReplyRate`) all zero for Mythic workspace — not currently used.
- `postLikes` and `profileViews` are warm-up/engagement actions, not conversion signals.

---

## MongoDB Fields (app side)

Campaigns with HeyReach are stored with:

| Field | Type | Description |
|---|---|---|
| `heyreach_campaign_id` | `int \| null` | HeyReach campaign ID to use as `campaignIds` filter |
| `heyreach_campaign_url` | `str \| null` | Direct link to campaign in HeyReach UI |
| `heyreach_status` | `str \| null` | `"created"` / `"failed"` / `null` |
| `smartlead_workspace` | `str` | Workspace key → maps to API key env var |
| `smartlead_client_name` | `str \| null` | Client name for filtering |

To get stats per campaign from your app:

```python
# 1. List campaigns with heyreach_campaign_id set
campaigns = db.precise_automator_campaigns.find(
    {"heyreach_campaign_id": {"$ne": None}}
)

# 2. For each campaign, call:
stats = get_overall_stats(campaignIds=[doc["heyreach_campaign_id"]])

# 3. Attach overallStats + byDayStats to response
```
