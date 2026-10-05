# SPDX-License-Identifier: Apache-2.0
"""Validate normative definitions, deterministic generation, and project license scopes."""
import ast,hashlib,json,pathlib,subprocess,sys
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate_spec
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
 paths=[ROOT/'spec/openapi.json',ROOT/'spec/schemas.json'];before=[p.read_bytes() for p in paths]
 subprocess.run([sys.executable,str(ROOT/'tools/build_spec.py')],check=True,capture_output=True)
 assert before==[p.read_bytes() for p in paths],'Generated assets differ from maintained source'
 api,schema=[json.loads(b) for b in before];validate_spec(api);Draft202012Validator.check_schema(schema)
 ids=[op['operationId'] for methods in api['paths'].values() for op in methods.values()];assert len(ids)==len(set(ids))
 audited=[]
 for path in sorted(ROOT.rglob('*.py')):
  if any(p in {'.venv','data','__pycache__'} for p in path.relative_to(ROOT).parts):continue
  raw=path.read_text(encoding='utf-8');tree=ast.parse(raw)
  expected='GPL-3.0-only' if path.relative_to(ROOT).parts[0]=='reference' else 'Apache-2.0'
  assert raw.splitlines()[0]=='# SPDX-License-Identifier: '+expected
  imports=[]
  for node in ast.walk(tree):
   if isinstance(node,ast.Import):imports.extend(x.name for x in node.names)
   if isinstance(node,ast.ImportFrom):imports.append(node.module or '')
  if expected=='Apache-2.0':assert not any(n.startswith('reference') for n in imports)
  assert not any('appflowy' in n.lower() or 'collab' in n.lower() for n in imports)
  audited.append(dict(path=path.relative_to(ROOT).as_posix(),license=expected,imports=imports))
 print(json.dumps(dict(status='PASS',openapi=api['openapi'],version=api['info']['version'],operations=len(ids),schemas=len(schema['$defs']),deterministic=True,source_audit=audited),indent=2))
if __name__=='__main__':main()
