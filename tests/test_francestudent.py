import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from francestudent_client import FrancestudentMcp

out = Path(__file__).resolve().parent.parent / "state" / "fs_test"
fs = FrancestudentMcp(output_dir=out)
print("USAGE", json.dumps(fs.usage(), ensure_ascii=False, indent=1))
res = fs.generate("Un petit carré bleu sur un fond blanc, style plat, aucune texte.",
                  count=1, aspect="square", output_dir=str(out), timeout=240)
print("GEN", json.dumps({k: v for k, v in res.items() if k != "content"}, ensure_ascii=False, indent=1))
fs.close()
