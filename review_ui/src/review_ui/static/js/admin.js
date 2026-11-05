// Admin pages. They are plain HTML forms and work without this script;
// it only adds conveniences:
//   [data-copy-from=id]   button copies the text of element #id (the new token)
//   [data-load-into=id]   file input puts the chosen file's text into textarea #id (teams CSV)
//   form[data-confirm]    asks before submitting (revoke, remove)
//   select[data-autosubmit] submits its form when changed (contacts filter)

for (const button of document.querySelectorAll("[data-copy-from]")) {
  button.addEventListener("click", async () => {
    const text = document.getElementById(button.dataset.copyFrom)?.textContent ?? "";
    try {
      await navigator.clipboard.writeText(text.trim());
      button.textContent = "Copiado ✓";
    } catch {
      // Clipboard blocked (e.g. not HTTPS): select the text so Ctrl+C works.
      const range = document.createRange();
      range.selectNodeContents(document.getElementById(button.dataset.copyFrom));
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
      button.textContent = "Selecionado: Ctrl+C";
    }
  });
}

for (const input of document.querySelectorAll("input[type=file][data-load-into]")) {
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    const target = document.getElementById(input.dataset.loadInto);
    if (file && target) target.value = await file.text();
  });
}

for (const form of document.querySelectorAll("form[data-confirm]")) {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
}

for (const select of document.querySelectorAll("select[data-autosubmit]")) {
  select.addEventListener("change", () => select.form.submit());
}
