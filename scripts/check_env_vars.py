"""Compare config.py env vars with .env.example."""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

with open(PROJECT_ROOT / "app" / "config.py", "r", encoding="utf-8") as f:
    config_text = f.read()

env_vars = set()
env_vars.update(re.findall(r'os\.getenv\("([A-Z_]+)"', config_text))
env_vars.update(re.findall(r'_env_bool\("([A-Z_]+)"', config_text))
env_vars.update(re.findall(r'_env_int\("([A-Z_]+)"', config_text))
env_vars.update(re.findall(r'_env_optional\("([A-Z_]+)"', config_text))
env_vars.update(re.findall(r'_env_bounded_int\("([A-Z_]+)"', config_text))
env_vars.update(re.findall(r'_env_choice\("([A-Z_]+)"', config_text))

with open(PROJECT_ROOT / ".env.example", "r", encoding="utf-8") as f:
    example_text = f.read()
example_vars = set(re.findall(r"^([A-Z_]+)=", example_text, re.MULTILINE))

print("=== In config.py but NOT in .env.example ===")
for v in sorted(env_vars - example_vars):
    print(f"  {v}")

print()
print("=== In .env.example but NOT in config.py ===")
for v in sorted(example_vars - env_vars):
    print(f"  {v}")

print()
print(f"Total config.py vars: {len(env_vars)}")
print(f"Total .env.example vars: {len(example_vars)}")

missing_from_example = env_vars - example_vars
if missing_from_example:
    sys.exit(1)
