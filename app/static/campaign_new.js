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

const dropzoneFile = document.querySelector("#dropzone-file");

fileInput?.addEventListener("change", async () => {
  const file = fileInput.files?.[0];
  if (!file) return;

  textArea.value = await file.text();
  if (!campaignName.value.trim()) {
    campaignName.value = titleFromFilename(file.name);
  }
  if (dropzoneFile) {
    dropzoneFile.textContent = `Loaded: ${file.name}`;
    dropzoneFile.hidden = false;
  }
});

// Messaging input mode toggle (Upload file <-> Paste text).
// Both inputs stay in the DOM so the form submits the same fields as before.
document.querySelectorAll(".seg-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.mode;
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".mode-pane").forEach((pane) =>
      pane.classList.toggle("hidden", pane.dataset.pane !== mode));
  });
});
