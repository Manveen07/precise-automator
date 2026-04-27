const fileInput = document.querySelector("#messaging-file");
const textArea = document.querySelector("#messaging-text");
const campaignName = document.querySelector("#campaign-name");

function titleFromFilename(filename) {
  return filename
    .replace(/\.[^.]+$/, "")
    .replace(/^Email Sequence Repository\s*[_-]\s*/i, "")
    .replace(/[_-]+/g, " ")
    .trim();
}

fileInput?.addEventListener("change", async () => {
  const file = fileInput.files?.[0];
  if (!file) return;

  textArea.value = await file.text();
  if (!campaignName.value.trim()) {
    campaignName.value = titleFromFilename(file.name);
  }
});
