import json, os, subprocess, tempfile, time, uuid, threading
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import jsonschema
ROOT=Path(__file__).parent
LOCK=threading.Lock()
MODEL='gpt-6.1-sol'
class Handler(BaseHTTPRequestHandler):
 def log_message(self, fmt, *args): print(fmt % args, flush=True)
 def reply(self, code, body):
  raw=json.dumps(body,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
 def do_GET(self):
  if self.path in ['/health','/v1/health']: self.reply(200,{'status':'ok','backend':'Codex CLI','model':MODEL})
  elif self.path=='/v1/models': self.reply(200,{'object':'list','data':[{'id':MODEL,'object':'model','owned_by':'local'}]})
  else: self.reply(404,{'error':{'message':'Unknown endpoint'}})
 def do_POST(self):
  if self.path!='/v1/chat/completions': return self.reply(404,{'error':{'message':'Unknown endpoint'}})
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=2000000: return self.reply(413,{'error':{'message':'Invalid request size'}})
   req=json.loads(self.rfile.read(size)); messages=req['messages']; tools={x['function']['name']:x['function'] for x in req.get('tools',[])}
   if req.get('stream'): return self.reply(400,{'error':{'message':'Streaming is not enabled'}})
   schema={'type':'object','additionalProperties':False,'required':['content','tool_calls'],'properties':{'content':{'type':['string','null']},'tool_calls':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['name','arguments'],'properties':{'name':{'type':'string',**({'enum':list(tools)} if tools else {})},'arguments':{'type':'string'}}},**({'maxItems':0} if not tools else {})}}}
   prompt='You are the language backend for a Valheim companion. Answer the supplied conversation as its assistant. Follow its system instructions and live state. Use only the supplied game functions, expressing calls in the output tool_calls array, with arguments as valid JSON object strings. Never call operating-system tools. Do not claim a game action succeeded before its tool result. Speak the player language, keep dialogue concise, and retain the companion identity supplied by the mod. Background is possibly outdated; live game state takes priority.\nBACKGROUND:\n'+(ROOT/'world-context.txt').read_text()+'\nCONVERSATION AND AVAILABLE FUNCTIONS:\n'+json.dumps({'messages':messages,'tools':req.get('tools',[]),'tool_choice':req.get('tool_choice','auto')},ensure_ascii=False)
   if not LOCK.acquire(timeout=3): return self.reply(429,{'error':{'message':'Companion is busy; try again shortly'}})
   try:
    with tempfile.TemporaryDirectory(prefix='request-',dir=ROOT) as tmp:
     p=Path(tmp); (p/'schema.json').write_text(json.dumps(schema))
     cmd=['codex','exec','--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--disable','shell_tool','--disable','unified_exec','--disable','apps','--disable','multi_agent','--disable','skill_search','--disable','skill_mcp_dependency_install','-c','project_doc_max_bytes=0','-c','web_search="disabled"','-c','model_reasoning_effort="low"','-m',MODEL,'-C',str(ROOT),'--output-schema',str(p/'schema.json'),'--output-last-message',str(p/'answer.json'),'--json','-']
     result=subprocess.run(cmd,input=prompt,text=True,capture_output=True,timeout=100)
     if result.returncode or not (p/'answer.json').exists(): raise RuntimeError('Codex request failed')
     answer=json.loads((p/'answer.json').read_text()); jsonschema.validate(answer,schema)
     calls=[]
     for call in answer['tool_calls']:
      args=json.loads(call['arguments']); jsonschema.validate(args,tools[call['name']].get('parameters',{'type':'object'}))
      calls.append({'id':'call_'+uuid.uuid4().hex,'type':'function','function':{'name':call['name'],'arguments':json.dumps(args,ensure_ascii=False)}})
     message={'role':'assistant','content':answer['content']}
     if calls: message['tool_calls']=calls
     self.reply(200,{'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),'model':MODEL,'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if calls else 'stop'}]})
     print('Completed request: messages=%d calls=%d'%(len(messages),len(calls)),flush=True)
   finally: LOCK.release()
  except subprocess.TimeoutExpired: self.reply(504,{'error':{'message':'Companion response timed out'}})
  except Exception as e:
   print('Request failed: '+type(e).__name__,flush=True); self.reply(502,{'error':{'message':'Companion backend failed; check local service log'}})
if __name__=='__main__': ThreadingHTTPServer(('127.0.0.1',4316),Handler).serve_forever()
