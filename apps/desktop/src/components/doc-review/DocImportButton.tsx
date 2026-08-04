import { open } from "@tauri-apps/plugin-dialog";
import { useDocReviewStore } from "@/store/docReviewStore";

export function DocImportButton(): JSX.Element {
  const register = useDocReviewStore((s) => s.register);

  const onImport = async (): Promise<void> => {
    const picked = await open({
      multiple: false,
      filters: [{ name: "文档", extensions: ["pdf", "docx", "txt", "md"] }],
    });
    if (typeof picked === "string") {
      const res = await register(picked);
      if (!res.ok) alert(`导入失败：${res.error ?? "未知错误"}`);
    }
  };

  return (
    <button
      type="button"
      onClick={onImport}
      style={{
        padding: "4px 12px",
        background: "#007acc",
        color: "#fff",
        borderRadius: 4,
      }}
    >
      导入文档
    </button>
  );
}
