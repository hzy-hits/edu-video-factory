import json
from pathlib import Path

path = Path("/home/ivena/cfa_factory/output/runs/OFFICIAL_2026_L1_V9/R1/20260105T001101/video_script.json")
data = json.loads(path.read_text())
out_path = path.parent / "script_zh_labeled.txt"

with out_path.open("w", encoding="utf-8") as f:
    for i, scene in enumerate(data.get("scenes", []), 1):
        f.write(f"## Scene {i}: {scene.get('beat', 'Unknown')}\n")
        spk = scene.get('speaker', 'Narrator')
        label = {"Professor": "教授", "Student": "学员", "Narrator": "旁白"}.get(spk, spk)
        f.write(f"【{label}】{scene.get('spoken_zh', '')}\n\n")

print(f"Generated: {out_path}")
