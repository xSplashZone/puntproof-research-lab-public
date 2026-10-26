"""Matched, signed-distribution diagnostics; money accounting remains separate."""
from collections import defaultdict
import math
import random


def identity(row):
    return (int(row['season']),int(row['week']),str(row['game_id']),row['gsis'],row['team'],row['channel'])


def distribution(support,pmf):
    if not support or len(support)!=len(pmf) or len(support)>10000:
        raise ValueError('INVALID_DISTRIBUTION_SHAPE')
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in [*support,*pmf]):
        raise ValueError('NONFINITE_DISTRIBUTION')
    if any(int(x)!=x for x in support) or any(a>=b for a,b in zip(support,support[1:])):
        raise ValueError('SIGNED_INTEGER_SUPPORT_REQUIRED')
    if min(pmf)<0 or not math.isclose(sum(pmf),1,abs_tol=1e-7):
        raise ValueError('INVALID_PROBABILITY_MASS')
    total=0.;cum=[]
    for p in pmf:total+=p;cum.append(total)
    def quantile(q):return next((s for s,p in zip(support,cum) if p>=q),support[-1])
    return {'mean':sum(s*p for s,p in zip(support,pmf)),'median':quantile(.5),'lower80':quantile(.1),'upper80':quantile(.9),'p_zero':sum(p for s,p in zip(support,pmf) if s==0)}


def metrics(support,pmf,y):
    d=distribution(support,pmf)
    if type(y) not in (int,float) or not math.isfinite(y) or int(y)!=y:raise ValueError('KNOWN_SIGNED_INTEGER_OUTCOME_REQUIRED')
    before_p=before_y=half_pair=0.
    for s,p in zip(support,pmf):
        half_pair+=p*(s*before_p-before_y);before_p+=p;before_y+=s*p
    err=d['mean']-y
    return {**d,'absolute_error':abs(err),'squared_error':err*err,'error':err,'crps':sum(p*abs(s-y) for s,p in zip(support,pmf))-half_pair,'covered80':int(d['lower80']<=y<=d['upper80']),'zero_brier':(d['p_zero']-int(y==0))**2}


def score_rows(forecasts,outcomes):
    fm={identity(x):x for x in forecasts};om={identity(x):x for x in outcomes}
    if len(fm)!=len(forecasts) or len(om)!=len(outcomes):raise ValueError('DUPLICATE_FORECAST_OR_OUTCOME')
    if set(fm)!=set(om):raise ValueError('SETTLEMENT_POPULATION_MISMATCH')
    rows=[];excluded=defaultdict(int)
    for key,f in fm.items():
        o=om[key];status=o.get('status')
        if status in ('UNKNOWN','NONAPPEARANCE'):
            excluded[status]+=1;continue
        if status!='APPEARED':raise ValueError('INVALID_APPEARANCE_STATUS')
        rows.append({'identity':list(key),'position':f['position'],'high_usage':bool(f.get('high_usage',False)),**metrics(f['support'],f['pmf'],o['yards'])})
    def aggregate(rr):
        n=len(rr)
        if not n:return {'n':0,'mae':None,'rmse':None,'crps':None,'coverage80':None,'zero_brier':None}
        return {'n':n,'mae':sum(x['absolute_error'] for x in rr)/n,'rmse':math.sqrt(sum(x['squared_error'] for x in rr)/n),'crps':sum(x['crps'] for x in rr)/n,'coverage80':sum(x['covered80'] for x in rr)/n,'zero_brier':sum(x['zero_brier'] for x in rr)/n}
    cohorts={'all':aggregate(rows),'high_usage':aggregate([r for r in rows if r['high_usage']])}
    for pos in sorted({r['position'] for r in rows}):cohorts[pos]=aggregate([r for r in rows if r['position']==pos])
    return {'status':'SCORED' if rows else 'NO_VERIFIED_OUTCOMES','issued':len(forecasts),'excluded':dict(excluded),'rows':rows,'cohorts':cohorts,'economic_performance':'ROI_NOT_ESTABLISHED'}


def paired_week_interval(baseline,candidate,*,draws=1000,seed=20261026):
    """Prespecified whole-week paired bootstrap; small week counts stay inconclusive."""
    a={tuple(r['identity']):r for r in baseline};b={tuple(r['identity']):r for r in candidate}
    if set(a)!=set(b) or len(a)!=len(baseline) or len(b)!=len(candidate):raise ValueError('MATCHED_POPULATION_REQUIRED')
    blocks=defaultdict(list)
    for k in a:blocks[k[:2]].append(b[k]['crps']-a[k]['crps'])
    mean=sum(x for v in blocks.values() for x in v)/len(a) if a else None
    if len(blocks)<8:return {'status':'INSUFFICIENT_WHOLE_WEEKS','weeks':len(blocks),'n':len(a),'mean_delta':mean,'lower':None,'upper':None}
    if type(draws) is not int or not 100<=draws<=2000:raise ValueError('BOUNDED_BOOTSTRAP_REQUIRED')
    rng=random.Random(seed);weeks=list(blocks.values());samples=[]
    for _ in range(draws):
        chosen=[rng.choice(weeks) for _ in weeks]
        samples.append(sum(sum(v) for v in chosen)/sum(len(v) for v in chosen))
    samples.sort()
    return {'status':'EXPLORATORY_PAIRED_WEEK_INTERVAL','confidence':.975,'weeks':len(weeks),'n':len(a),'mean_delta':mean,'lower':samples[int(.0125*draws)],'upper':samples[min(draws-1,int(.9875*draws))],'draws':draws,'seed':seed}


def monitor(store,report,run_id):
    """Run only on already authorized scored records; does not read source outcomes."""
    all_rows=report['cohorts']['all'];n=all_rows['n']
    if n<30:
        store.alert('PERFORMANCE_LOW_VOLUME','P2','Performance evidence is still too small',{'actual':n,'expected':'At least30 settled observations before pooled calibration warning','next_action':'Accumulate new verified whole-week outcomes; do not retune on this small sample'},run_id)
    elif all_rows['coverage80']<.65:
        store.alert('DISTRIBUTION_OVERCONFIDENCE','P1','Observed interval coverage deteriorated',{'actual':all_rows['coverage80'],'expected':'Nominal80% interval; warning below65% with n>=30','n':n,'next_action':'Inspect zero/tail and high-usage cohorts. Keep challenger unpromoted pending matched chronological evidence.'},run_id)
    excluded=sum(report['excluded'].values())
    if excluded:
        store.alert('UNRESOLVED_OR_NONAPPEARANCE','P1','Some issued forecasts are not scored or learned',{'actual':report['excluded'],'expected':'UNKNOWN and NONAPPEARANCE remain separate from zero','next_action':'Review completion and participation witnesses; preserve the issued population'},run_id)
    return {'n':n,'verified_improvement':False,'promotion':'DISABLED','genuine_roi':'ROI_NOT_ESTABLISHED'}
