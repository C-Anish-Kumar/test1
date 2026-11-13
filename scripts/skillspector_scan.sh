#!/usr/bin/env bash
# Scan every agent skill and MCP config in this repo with NVIDIA SkillSpector.
#
#   scripts/skillspector_scan.sh [OUT_DIR]
#
# A skill is any directory holding a SKILL.md under .claude/skills, .agents/skills
# or skills/. Each is scanned on its own; if .github/skillspector/<skill>.yaml
# exists it is used as that skill's baseline of reviewed, accepted findings.
# MCP configs (.mcp.json and friends) are scanned too. Any active finding fails.
# Static analysis only (--no-llm): file contents never leave the machine.
set -uo pipefail

out_dir="${1:-skillspector-reports}"
mkdir -p "$out_dir"
baseline_dir=".github/skillspector"
status=0

scan() { # name target [extra args...]
  local name="$1" target="$2"; shift 2
  echo "::group::SkillSpector: $target"
  skillspector scan "$target" --no-llm --fail-on-findings "$@" || status=1
  skillspector scan "$target" --no-llm "$@" --format sarif --output "$out_dir/$name.sarif" >/dev/null || true
  skillspector scan "$target" --no-llm "$@" --format markdown --output "$out_dir/$name.md" >/dev/null || true
  echo "::endgroup::"
}

found=0
while IFS= read -r skill_md; do
  dir="$(dirname "$skill_md")"
  name="$(basename "$dir")"
  args=()
  [[ -f "$baseline_dir/$name.yaml" ]] && args=(--baseline "$baseline_dir/$name.yaml")
  scan "skill-$name" "$dir" "${args[@]}"
  found=1
done < <(find .claude/skills .agents/skills skills -mindepth 2 -maxdepth 2 -name SKILL.md 2>/dev/null | sort)

for cfg in .mcp.json .cursor/mcp.json .vscode/mcp.json; do
  [[ -f "$cfg" ]] || continue
  scan "mcp-$(echo "$cfg" | tr '/.' '__')" "$cfg"
  found=1
done

[[ $found -eq 1 ]] || echo "No skills or MCP configs found; nothing to scan."
if [[ $status -ne 0 ]]; then
  echo "SkillSpector reported active findings. Fix them, or after review add a"
  echo "rule with a reason to $baseline_dir/<skill>.yaml."
fi
exit $status
