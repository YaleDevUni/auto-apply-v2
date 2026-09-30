import { Upload } from "lucide-react";
import { useRef } from "react";
import { Button } from "@/components/ui/button";

// 파일 하나를 골라 넘긴다. 같은 파일을 연달아 골라도 onChange 가 다시 불리게 창을 열기 직전에 값을 비운다.
export function FileUploadButton({
  accept,
  label,
  pendingLabel,
  pending,
  onFile,
}: {
  accept: string;
  label: string;
  pendingLabel: string;
  pending: boolean;
  onFile: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={accept}
        className="hidden"
        aria-hidden
        tabIndex={-1}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
        }}
      />
      <Button type="button" onClick={() => {
          if (!input.current) return;
          input.current.value = "";
          input.current.click();
        }} disabled={pending}>
        <Upload />
        {pending ? pendingLabel : label}
      </Button>
    </>
  );
}
