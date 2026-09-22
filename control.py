"""CLI and plugin control operations. State stays outside the repository."""
import argparse
import json
import sys
import time
from bridge import Desktop
from monitor import Store, state_directory
from core import normalize_usage


def execute(action, thread_id=None, owner=None):
    store=Store()
    try:
        if action=='init':
            if not owner:raise ValueError('A real Codex management task ID is required')
            store.set('owner',owner)
            return {'initialized':True,'state_directory':str(store.directory)}
        if action=='status':
            return {**store.last_report(),'enabled':store.enabled(),'attempts':store.attempts()}
        if action=='doctor':
            # Prints its own report and exits non-zero when a check fails, so
            # scripts can gate on "will this start with Codex". argv must be
            # empty or argparse would re-parse this CLI's own arguments.
            import doctor
            return doctor.main(argv=[])
        if action in ('enable','pause'):
            store.set_enabled(action=='enable')
            (store.directory/'refresh.request').touch()
            return {'enabled':store.enabled()}
        if action=='refresh':
            (store.directory/'refresh.request').touch()
            return {'refresh_requested':True}
        if action=='stop':
            (store.directory/'stop.request').touch()
            return {'stop_requested':True}
        if action=='unmark':
            if not thread_id:raise ValueError('thread_id required')
            store.unmark(thread_id)
            return {'unmarked':thread_id}
        desktop=Desktop(store.get('owner',''))
        try:
            if action=='probe':
                return {'usage':normalize_usage(desktop.call('get_usage_limits'),time.time()),
                        'tasks':desktop.call('list_threads',limit=30)}
            if action=='mark':
                if not thread_id:raise ValueError('thread_id required')
                listing=desktop.call('list_threads',limit=30)
                rows=listing.get('threads',[])+listing.get('pinnedThreads',[])
                if not any(t.get('id')==thread_id and t.get('kind')=='codex' and t.get('hostId','local')=='local' for t in rows):
                    raise ValueError('Task must be an unarchived local Codex task in the monitored list')
                detail=desktop.call('read_thread',threadId=thread_id,turnLimit=1,includeOutputs=False,maxOutputCharsPerItem=1)
                if not detail.get('turns'):raise ValueError('Task has no latest turn')
                turn=detail['turns'][0]
                if turn.get('status') not in ('completed','failed'):raise ValueError('Task is running, interrupted or awaiting input')
                store.mark(thread_id,turn['id'])
                (store.directory/'refresh.request').touch()
                return {'marked':thread_id,'turn_id':turn['id'],'resumes_when_quota_available':True}
            raise ValueError('Unknown action')
        finally:desktop.close()
    finally:store.close()


def main():
    parser=argparse.ArgumentParser(description='Codex quota recovery controls')
    parser.add_argument('action',choices=['init','status','doctor','enable','pause','refresh','stop','probe','mark','unmark'])
    parser.add_argument('--thread-id');parser.add_argument('--owner')
    args=parser.parse_args()
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    try:
        result=execute(args.action,args.thread_id,args.owner)
        # doctor prints its own report and signals failure through the exit code.
        if isinstance(result,int):return result
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False));return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
