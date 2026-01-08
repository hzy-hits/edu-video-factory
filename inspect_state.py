import json
from pathlib import Path

p = Path("/home/ivena/cfa_factory/output/runs/OFFICIAL_2026_L1_V9/R1/20260105T001101/state.json")
data = json.loads(p.read_text())

print("Keys:", list(data.keys()))
if "lesson_plan" in data:
    print("\nL Plan:", json.dumps(data["lesson_plan"], indent=2))

if "professor_claims" in data:
    claims = data["professor_claims"].get("first_principles_claims", [])
    print(f"\nProf Claims: {len(claims)}")

if "student_challenges" in data:
    attacks = data["student_challenges"].get("student_attacks", [])
    print(f"\nStud Attacks: {len(attacks)}")
    
if "editor_script" in data:
    print(f"\nEditor Script Type: {type(data['editor_script'])}")
    if isinstance(data["editor_script"], dict):
        print(f"Editor Scenes: {len(data['editor_script'].get('scenes', []))}")
