"""Small display strings, independent of the GUI toolkit."""
def countdown(reset,now):
    seconds=max(0,int(reset-now))
    if seconds==0:return '更新中'
    days,seconds=divmod(seconds,86400)
    hours,seconds=divmod(seconds,3600)
    minutes=seconds//60
    if days:return f'{days}d {hours}h'
    if hours:return f'{hours}h {minutes:02d}m'
    return f'{minutes}m' if minutes else '<1m'


def display_state(report,now):
    usage=report.get('usage',{})
    age=now-usage.get('observed_at',0)
    stale=not report.get('connected') or not 0<=age<=60
    windows=usage.get('windows',[])
    groups=[]
    for minutes,label in [(300,'5h'),(10080,'7d')]:
        window=next((w for w in windows if w.get('minutes')==minutes),None)
        groups.append({'label':label,'balance':f"{round(100-window['used'])}%" if window else '—',
                       'countdown':countdown(window['reset'],now) if window else '连接中'})
    uncertain=any(a.get('status') in ('uncertain','dispatching') for a in report.get('attempts',[]))
    if stale:color,status='#fbbf24','连接中 / 数据过期，暂停续跑'
    elif uncertain:color,status='#fbbf24','发送结果待检查'
    elif not report.get('enabled'):color,status='#94a3b8','自动续跑已暂停'
    elif report.get('candidates'):color,status='#7dd3fc',f"{len(report['candidates'])} 个任务等待恢复"
    elif any(i.get('reason')=='read_failed' for i in report.get('inspections',[])):
        color,status='#fbbf24','部分任务读取失败 · 检查不完整'
    elif report.get('scan_may_be_truncated'):
        color,status='#fbbf24','仅检查最近任务与置顶任务 · 扫描范围受限'
    else:color,status='#6ee7b7','监测正常 · 未发现待恢复任务'
    return {'groups':groups,'color':color,'status':status,'stale':stale}
