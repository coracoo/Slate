# -*- coding: utf-8 -*-
"""工作台串行队列：一次一任务，下载后精确回写 job，不按图片内容猜 ID。"""
from pathlib import Path
import threading
import uuid
import chatgpt_runs as runs
import image_use_runtime as runtime

_LOCK=threading.RLock()
_WORKERS={}
_STOP={}

def status(project,run_id):
    result=runs.get_run(project,run_id)
    result['worker_active']=run_id in _WORKERS and _WORKERS[run_id].is_alive()
    return result

def start(project,run_id,token):
    with _LOCK:
        run=runs.get_run(project,run_id)
        # 验证权限先于启动线程。
        runs._auth(runs._read(runs._run_path(project,run_id)),token)
        if run['status'] in {'cancelled','done','failed'}:
            raise runs.RunStateError('运行已结束')
        if run_id in _WORKERS and _WORKERS[run_id].is_alive():
            return run
        if any(w.is_alive() for w in _WORKERS.values()):
            raise runs.RunStateError('已有串行队列正在使用浏览器')
        if not runtime.status()['installed']:
            raise runs.RunStateError('请先安装 image-use / chrome-use')
        _STOP.pop(run_id,None)
        worker=threading.Thread(target=_execute,args=(project,run_id,token),daemon=True)
        _WORKERS[run_id]=worker; worker.start()
        return run

def control(project,run_id,token,action):
    runs._auth(runs._read(runs._run_path(project,run_id)),token)
    with _LOCK:
        active=run_id in _WORKERS and _WORKERS[run_id].is_alive()
        if action in {'pause','cancel'} and active:
            # 不破坏正在生成的外部请求；本张保存后停，不启动下一张。
            _STOP[run_id]=action
            with runs._LOCK:
                path=runs._run_path(project,run_id)
                result=runs._read(path)
                result['stop_requested']=action
                result['revision']+=1
                runs._write(path,result)
            return status(project,run_id)
        if action=='resume':
            if active: return runs.get_run(project,run_id)
            runs.set_run_control(project,run_id,token,'resume')
            return start(project,run_id,token)
        return runs.set_run_control(project,run_id,token,action)

def prompt_for(attempt):
    mapping='\n'.join(f'图片{i}（{r["reference_id"]}）：{r.get("purpose") or "视觉参考"}；资产 {r.get("asset_ref","")}'
                       for i,r in enumerate(attempt.get('references') or [],1))
    ratio=attempt['generation_contract'].get('aspect_ratio','16:9')
    return '\n'.join(['请创作当前任务的一张独立高清图片。',mapping,
        attempt['prompt_assembled'],'禁止内容：'+attempt.get('negative',''),
        f'画幅 {ratio}，仅输出一张完整原图。遵循当前任务内部的资产视图要求；不要混入其他任务、不要编号或水印。'])

def _execute(project,run_id,token):
    attempt=None
    def event(kind,payload):
        return runs.record_event(project,run_id,attempt['attempt_id'],token,uuid.uuid4().hex,kind,payload)
    try:
        while True:
            if _STOP.get(run_id):
                runs.set_run_control(project,run_id,token,_STOP.pop(run_id)); return
            attempt=runs.claim_next(project,run_id,token)
            if not attempt: return
            if attempt['phase'] in {'waiting_dependencies','needs_review','paused'}: return
            if attempt['phase'] not in {'ready','generating','staged','importing'}:
                raise runs.RunStateError('上次执行中断，状态需核对，禁止自动重新发送')
            folder=Path(runs._run_dir(project,run_id))/'executor'/attempt['attempt_id']
            folder.mkdir(parents=True,exist_ok=True)
            output=folder/'result.png'
            if attempt['phase']=='ready':
                refs=[]
                for i,ref in enumerate(attempt.get('references') or [],1):
                    data=runs.open_reference(project,run_id,attempt['attempt_id'],ref['reference_id'],token)
                    path=folder/f'R{i}{Path(data["file_name"]).suffix}'
                    path.write_bytes(data['content']); refs.append(str(path))
                # 这些是执行阶段，不宣称网页附件已验证；上游负责真正上传。
                for phase in ('uploading','preparing','generating'):
                    attempt=event('phase',{'phase':phase})
                runtime.generate_image(prompt_for(attempt),output,refs=refs)
            if attempt['phase']=='generating':
                if not output.is_file():
                    raise runs.RunStateError('已有发送记录但未获得图片；请检查保留的 ChatGPT 会话，禁止自动重绘')
                current=runs.get_run(project,run_id)
                staged=runs.stage_result(project,run_id,attempt['attempt_id'],token,output.read_bytes(),
                    attempt.get('output_spec',{}).get('filename') or 'result.png',
                    {'executor':'image-use','source':'single-request'},current['revision'])
                if staged.get('phase')=='needs_review': return
            result=runs.import_staged_result(project,run_id,attempt['attempt_id'],token)
            current=runs.get_run(project,run_id)
            if current['status'] in {'needs_review','paused','done','cancelled'}: return
    except Exception as exc:
        if attempt:
            try: event('send_unknown',{'reason':str(exc)[:1000]})
            except runs.RunError: pass
        else:
            runs.set_run_control(project,run_id,token,'pause',str(exc)[:1000])
