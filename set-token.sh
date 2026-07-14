#!/usr/bin/env bash
# Local-testing helper — sets the HA token in a local options.json.
# The HA add-on itself needs none of this; set the token in the HA add-on Config tab.
#
#   ./set-token.sh set     # prompts (hidden input), writes token
#   ./set-token.sh clear   # blanks the token
#   ./set-token.sh show    # shows whether a token is set (never prints it)

set -euo pipefail
CFG="${DASHSNAP_OPTIONS:-/tmp/dashsnap_options.json}"

ensure_cfg() {
  [[ -f "$CFG" ]] || cp options.sample.ha.json "$CFG"
}

set_token() {
  read -rs -p "Paste long-lived token (input hidden): " tok; echo
  [[ -n "$tok" ]] || { echo "empty — aborted"; exit 1; }
  # Works with both flat {token: ...} and nested {auth: {token: ...}} forms
  DS_TOK="$tok" python3 -c "
import json, os
p = '$CFG'
d = json.load(open(p))
if 'auth' in d:
    d['auth']['token'] = os.environ['DS_TOK']
else:
    d['token'] = os.environ['DS_TOK']
json.dump(d, open(p, 'w'), indent=2)
"
  echo "token set in $CFG"
}

clear_token() {
  python3 -c "
import json
p = '$CFG'
d = json.load(open(p))
if 'auth' in d:
    d['auth']['token'] = ''
else:
    d['token'] = ''
json.dump(d, open(p, 'w'), indent=2)
"
  echo "token cleared in $CFG"
}

show() {
  python3 -c "
import json
d = json.load(open('$CFG'))
t = d.get('auth', {}).get('token') or d.get('token', '')
print('token: SET (' + str(len(t)) + ' chars)' if t else 'token: NOT set')
"
}

ensure_cfg
case "${1:-show}" in
  set) set_token ;;
  clear) clear_token ;;
  show) show ;;
  *) echo "usage: $0 {set|clear|show}"; exit 1 ;;
esac
