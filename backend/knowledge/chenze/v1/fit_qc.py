"""Single-span candidate QC. Pure computation: never trims media or approves publication."""
from decimal import Decimal, InvalidOperation
import json, argparse

def evaluate(start, end, limit, *, precision='coarse_model', context_verified=False, sentence_tail_verified=False, asr_human_verified=False):
    try:
        a,b,cap=(Decimal(str(x)) for x in [start,end,limit])
    except (InvalidOperation,ValueError,TypeError):
        return {'status':'INVALID_INTERVAL','reason':'start/end/limit必须为有限数值','automatic_trim':False}
    if not all(x.is_finite() for x in [a,b,cap]) or a<0 or b<=a or cap<=0:
        return {'status':'INVALID_INTERVAL','reason':'要求0<=start<end且limit>0，数值必须有限','automatic_trim':False}
    duration=b-a
    out={'start_seconds':str(a),'end_seconds':str(b),'limit_seconds':str(cap),'duration_seconds':str(duration),'arithmetic_fits':duration<=cap,'automatic_trim':False,'publication_approved':False,'scope':'仅候选时间/完整语义门槛，不验证版权或发布授权'}
    if duration>cap:
        return dict(out,status='REJECT_OVER_LIMIT',reason='end-start大于limit；撤销FIT，不自动截断；回溯完整句尾/重选，仍不足返回NO_FIT')
    if precision!='human_verified_original_timeline' or not all([context_verified,sentence_tail_verified,asr_human_verified]):
        return dict(out,status='NEEDS_HUMAN_REVIEW',reason='时间算术通过不等于独立完整单元；须原片人工核边界、必要上下文、句尾和ASR')
    return dict(out,status='FIT',reason='仅候选时间/语义质检通过；不代表风格有效或许可发布')

if __name__=='__main__':
    q=argparse.ArgumentParser();q.add_argument('--start',required=True);q.add_argument('--end',required=True);q.add_argument('--limit',required=True);q.add_argument('--precision',default='coarse_model');q.add_argument('--context-verified',action='store_true');q.add_argument('--sentence-tail-verified',action='store_true');q.add_argument('--asr-human-verified',action='store_true');a=q.parse_args()
    print(json.dumps(evaluate(a.start,a.end,a.limit,precision=a.precision,context_verified=a.context_verified,sentence_tail_verified=a.sentence_tail_verified,asr_human_verified=a.asr_human_verified),ensure_ascii=False,indent=2))
