from datetime import date
def balance(sent,received,returned,in_transit):
    vals=[sent,received,returned,in_transit]
    if any(not isinstance(v,int) or v<0 for v in vals):raise ValueError('数量必须为非负整数')
    return sent-received-returned-in_transit
def authorized(grant,signer,site,on):
    return grant['signer']==signer and grant['site']==site and date.fromisoformat(grant['start'])<=date.fromisoformat(on)<=date.fromisoformat(grant['end'])
def overused(allocations,capacities):
    totals={}
    for a in allocations:
        if not isinstance(a['qty'],int) or a['qty']<0:raise ValueError('分配数量必须为非负整数')
        totals[a['receipt']]=totals.get(a['receipt'],0)+a['qty']
    return sorted(k for k,v in totals.items() if v<0 or v>capacities.get(k,0))
TOOLS={'balance':balance,'authorized':authorized,'overused':overused}
