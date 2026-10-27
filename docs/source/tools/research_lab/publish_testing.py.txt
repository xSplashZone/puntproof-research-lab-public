"""Allowlisted public investigation export. Never copies a private runtime tree."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import zipfile
import subprocess

ROOT=Path(__file__).resolve().parents[2]

def public_research(g):
    numeric_keys=('n','mae','crps','coverage80','interval80_width','median_mae','threshold_brier')
    def metrics(value):return {k:value[k] for k in numeric_keys if isinstance(value,dict) and type(value.get(k)) in (int,float)}
    continuous=g.get('continuous',{});attempts=[]
    for i,a in enumerate(continuous.get('attempts',[])):
        p=a.get('plan',{});r=a.get('result');safe=None
        if r:
            f=r.get('frozen_controls') or {};safe={'metrics':metrics(r.get('metrics')),'matched_candidate_metrics':metrics(r.get('matched_candidate_metrics')),'completed_folds':r.get('completed_folds',0),'probability_status':'EXPERIMENTAL_UNVALIDATED','classification':r.get('classification'),'frozen_controls':{'same_population_n':f.get('same_population_n'),'candidate':metrics(f.get('candidate')),'F':metrics(f.get('F')),'B':metrics(f.get('B'))}}
        attempts.append({'id':'aggregate-'+str(i+1),'plan':{k:p.get(k) for k in ('template_id','process','family','channel')},'status':a.get('status'),'elapsed_seconds':a.get('elapsed_seconds'),'phase':a.get('phase'),'result':safe})
    forward=continuous.get('forward',{});summary=continuous.get('source',{}).get('summary',{})
    return {k:int(g.get(k,0) or 0) for k in ('completed','fitted_artifacts','failed','rejected')}|{'attempts':[],'continuous':{'attempts':attempts,'runtime':{'status':'PUBLIC_SNAPSHOT','next_action':'Read-only capture. Local work continues separately.'},'budget':{},'processes':{'B':continuous.get('processes',{}).get('B')},'forward':{k:forward.get(k) for k in ('status','games','forecasts','targets','exclusions')},'source':{'summary':{k:v for k,v in summary.items() if type(v) in (int,float) or v is None}},'paused':True}}

def public_state(state,at):
    counts={key:int(state['home']['counts'].get(key,0)) for key in ('seeds_loaded','source_records_validated','forecasts_generated','forecasts_settled','learning_updates','candidates_tested')}
    totals=[]
    for a in state.get('coverage_audits',[]):
        totals.append({k:a[k] for k in ('total','eligible','excluded','reasons','cohorts','denominator','input_equivalence') if k in a})
    message='Published investigation snapshot captured '+at+'. This cloud copy does not run the local Python engine or worker.'
    return dict(cloud_mode=True,captured_at=at,
      home=dict(headline='Public investigation copy',summary=message,counts=counts,
        worker={'label':'Snapshot only','detail':'The operational V3 engine remains on the owner’s PC. This hosted copy exposes the interface and reviewed source code.'},
        last_real={},next={'label':'Inspect the public source or test the interface','reason':'This snapshot reports saved completed work at capture. Public source: /source/index.html'},
        primary_action={'label':'Local operations are unavailable in this copy','enabled':False,'reason':'No private engine, data, credentials or control connection is hosted.'},
        performance={'summary':'Not measured yet' if not counts['forecasts_settled'] else 'See private local measured results','reason':'No private forecast, outcome or betting records are publicly redistributed.','metrics':[]}),
      overview={'status':'PUBLIC_READ_ONLY_INVESTIGATION','genuine_roi':'ROI_NOT_ESTABLISHED','production_model':'UNVERIFIED','provider_access':'DISABLED'},
      function_status=[dict(id='forecast',label='Forecasting',status='LOCAL_ONLY',summary='Individual private predictions are omitted from the public export.')],
      attention=[dict(id='cloud-scope',title='The hosted copy is for interface and code investigation',summary=message+' Full cloud V3 execution requires a separate Python-capable host with private storage and admitted engine/data access.',status='OPEN',owner_required=False,blocks=['Cloud V3 execution'],source='Hosting capability',resolution_kind='hosting')],
      coverage_audits=totals,candidate_decisions=[],forecasts=[],performance=[],experiments=[],experiment_results=[],knowledge=[],knowledge_sources=[],models=[],leaderboard=[],alerts=[],sources=[],integrations=[],packages=[],jobs=[],settings={},receipts=[],bets=[],ledger={'REAL':None,'PAPER':None},runtime={},gamd=public_research(state.get('gamd',{})))

def build(destination,state,at):
    destination=Path(destination);dist=destination/'dist';dist.mkdir(parents=True,exist_ok=True)
    for name in ('index.html','style.css','app.js'):(dist/name).write_bytes((ROOT/'tools/research_lab/static'/name).read_bytes())
    p=dist/'index.html';s=p.read_text(encoding='utf8').replace('<body>','<body><aside style="padding:12px 24px;background:#173c40;color:#fff">Public read-only investigation copy · <a href="/source/index.html" style="color:#fff">Browse source</a> · <a href="/source/research-lab-source.zip" style="color:#fff">Download code</a> · Snapshot '+html.escape(at)+'</aside>');p.write_text(s,encoding='utf8',newline='\n')
    p=dist/'app.js';s=p.read_text(encoding='utf8');s=s.replace("async function request(url,options={}){", "async function request(url,options={}){if(url==='/api/state')url='/snapshot.json';")
    s=s.replace("async function act(action,fields={}){", "async function act(action,fields={}){notice('This public copy is read-only. Inspect /source/index.html; private local operations are unavailable.',false);return null;")
    s=s.replace('Connected to your local Lab','Public snapshot — no live local connection');p.write_text(s,encoding='utf8',newline='\n')
    safe=public_state(state,at);(dist/'snapshot.json').write_text(json.dumps(safe,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    files=sorted((ROOT/'tools/research_lab/gamd').glob('*.py'))+sorted((ROOT/'tools/research_lab').glob('*.py'))+sorted((ROOT/'tools/research_lab/static').glob('*'))
    files += [ROOT/'tools/research_lab/launch.ps1',ROOT/'Open Research Lab.cmd',ROOT/'src/nfl_model/research_lab_gate.py',ROOT/'src/nfl_model/research_lab_pair_gate.py']
    files += [ROOT/'src/nfl_model'/name for name in ('gamd_gate.py','licensed_bdl_gate.py','continuous_gamd_gate.py')]
    files += sorted((ROOT/'tests/research_lab').glob('test_*.py'))
    files=[p for p in files if p.is_file() and not p.is_symlink()]
    source=dist/'source';source.mkdir(exist_ok=True);manifest=[];links=[]
    with zipfile.ZipFile(source/'research-lab-source.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for p in files:
            name=p.relative_to(ROOT).as_posix();data=p.read_bytes();entry=zipfile.ZipInfo(name,date_time=(2026,10,27,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(entry,data)
            target=source/(name+'.txt');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            manifest.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
            links.append('<li><a href="'+html.escape(name)+'.txt">'+html.escape(name)+'</a></li>')
        readme='PuntProof Research Lab public application source. Private engine seeds, football payloads, policy/evidence records, credentials and production configuration are excluded. Existing local install remains authoritative. Public host serves the existing UI with a sanitized aggregate snapshot; it does not run Python/V3. For authored portable arithmetic/persistence tests: PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_core.py (also test_monitoring.py and test_experiments.py). No package installation is required for those standard-library checks. Full integration requires the existing admitted local repository, supplied engine and pinned isolated runtime. No live betting or improvement claim follows from a test.\n'
        archive.writestr('PUBLIC_README.txt',readme)
    (source/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
    commit=subprocess.check_output([r'C:\Program Files\Git\cmd\git.exe','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    (source/'export-custody.json').write_text(json.dumps({'private_source_commit':commit,'exported_at':at,'manifest_sha256':hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),'private_records_exported':0},indent=2)+'\n',encoding='utf8')
    (source/'README.txt').write_text(readme,encoding='utf8')
    (source/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Research Lab public source</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px;background:#101b22;color:#e6edf3}a{color:#7de1da}li{margin:8px}pre{white-space:pre-wrap}</style><h1>PuntProof Research Lab source</h1><p><a href="/">Open investigation interface</a> · <a href="research-lab-source.zip">Download source ZIP</a> · <a href="manifest.json">SHA-256 inventory</a></p><pre>'+html.escape(readme)+'</pre><ul>'+''.join(links)+'</ul></html>',encoding='utf8')
    return {'files':len(files),'source_archive_sha256':hashlib.sha256((source/'research-lab-source.zip').read_bytes()).hexdigest(),'captured_at':at,'private_records_exported':0}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,required=True);p.add_argument('--state',type=Path,required=True);p.add_argument('--at',required=True);a=p.parse_args();print(json.dumps(build(a.destination,json.loads(a.state.read_bytes()),a.at)))
