<%*
// Obsidianizer: обновить карточку текущей папки (Templater-шаблон).
// Замените {{CLI_PATH}} на реальный путь к Obsidianizer перед использованием.
try {
  const tFile = tp.config.target_file;
  const base = app.vault.adapter.getBasePath();
  const rel = tFile.parent.path;
  const full = base + "/" + rel;
  const { exec } = require("child_process");
  const fs = require("fs");
  const cli = "{{CLI_PATH}}";
  exec(
    `"${cli}" folders --path "${full}" --no-recursive --adopt --vault-root "${base}" --rel "${rel}"`,
    (error) => {
      if (error) {
        new Notice("Obsidianizer: ошибка обновления — " + error.message);
        return;
      }
      try {
        const text = fs.readFileSync(full + "/" + tFile.name, "utf8");
        const g = text.includes("## Gallery") ? "✓" : "✗";
        const i = text.includes("## Images") ? "✓" : "✗";
        new Notice("Obsidianizer: обновлено · Gallery " + g + " · Images " + i);
      } catch (e) {
        new Notice("Obsidianizer: карточка обновлена");
      }
    }
  );
} catch (e) {
  new Notice("Obsidianizer: " + e.message);
}
%>
