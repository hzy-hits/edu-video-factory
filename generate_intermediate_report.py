import json
from pathlib import Path

path = Path("/home/ivena/cfa_factory/output/runs/OFFICIAL_2026_L1_V9/R1/20260105T001101/state.json")
data = json.loads(path.read_text())
report_path = path.parent / "intermediate_report.md"

with report_path.open("w", encoding="utf-8") as f:
    f.write("# Intermediate Outputs Report\n\n")
    
    f.write("## 1. Router Plan\n")
    f.write(f"Recommended Depth: {data.get('lesson_plan', {}).get('recommended_depth')}\n")
    f.write(f"Target Minutes: {data.get('lesson_plan', {}).get('segment_minutes')}\n\n")
    
    f.write("## 2. Professor Claims\n")
    pc = data.get("professor_claims")
    f.write(f"```json\n{json.dumps(pc, indent=2, ensure_ascii=False)}\n```\n\n")
    
    f.write("## 3. Student Challenges\n")
    sc = data.get("student_challenges")
    if isinstance(sc, str):
        f.write(f"{sc}\n\n")
    elif sc:
        f.write(f"```json\n{json.dumps(sc, indent=2, ensure_ascii=False)}\n```\n\n")
    else:
        f.write("(No Student Challenges)\n\n")
        
    f.write("## 4. Synthesis\n")
    syn = data.get("synthesis_claims")
    f.write(f"```json\n{json.dumps(syn, indent=2, ensure_ascii=False)}\n```\n\n")

print(f"Generated: {report_path}")
