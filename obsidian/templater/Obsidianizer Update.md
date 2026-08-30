<%*
// Obsidianizer: обновить карточку только текущей папки (Templater-шаблон).
// Использует child_process (desktop Obsidian). Путь к обёртке CLI указан
// полностью — установка пакета в PATH не требуется. Корень vault и
// vault-relative путь папки подставляются автоматически.
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
      // Самодиагностика: проверяем, что галерея/архив сгенерированы.
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
