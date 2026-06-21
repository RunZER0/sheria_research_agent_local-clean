import subprocess
import os


class PdfRenderer:
    @staticmethod
    def from_docx(docx_path: str, output_dir: str) -> str:
        """Converts docx to PDF server-side using headless LibreOffice."""
        try:
            cmd = [
                "soffice", "--headless",
                "--convert-to", "pdf",
                "--outdir", output_dir,
                docx_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            base_name = os.path.splitext(os.path.basename(docx_path))[0]
            return os.path.join(output_dir, f"{base_name}.pdf")
        except FileNotFoundError:
            raise RuntimeError(
                "LibreOffice not found. Install LibreOffice and ensure 'soffice' is on the system PATH."
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Headless LibreOffice compilation engine fault: {e.stderr.decode()}")
        except Exception as e:
            raise RuntimeError(f"Headless LibreOffice compilation engine fault: {str(e)}")
