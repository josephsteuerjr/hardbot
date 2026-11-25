/** Existing administrator engine, isolated by confirmed CRM authority. */
import {app,BrowserWindow,clipboard,dialog,shell} from 'electron';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createHash} from 'node:crypto';
import {appendFileSync,existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {basename,dirname,extname,join,resolve} from 'node:path';
import {fetchBuffered} from './transport';

export type AnalystAuthority={owner:string;actor:number;url:string;token:string;fingerprint:string;existingProfile?:boolean;profileUrl?:string;current:()=>boolean};
type Options={home:string;window:()=>BrowserWindow|null;authorize:(expected:string)=>Promise<AnalystAuthority>;
  writeTable:(report:any,path:string)=>Promise<any>;synthetic:()=>boolean};
const localRoutes=new Set(['/state','/busy','/fn','/job','/jobs','/admin','/admin_image','/mail/state','/mail/set','/mail/send']);
const scalar=(v:unknown,max=500)=>{if(typeof v!=='string'||!v||v.length>max)throw Error('Недопустимое значение.');return v;};

export class AnalystRuntime{
  private child:ChildProcessWithoutNullStreams|null=null;
  private port=0;private secret='';private authority='';private starting:Promise<void>|null=null;
  private files=new Set<string>();
  private filesOwner='';
  constructor(private options:Options){app.once('will-quit',()=>this.stop());}
  stop(){this.child?.kill();this.child=null;this.port=0;this.secret='';this.authority='';this.starting=null;}
  async resume(expected:string) {
    const subject=await this.options.authorize(expected);
    if (subject.current()) await this.start(subject);
  }
  async busy():Promise<boolean>{
    if(this.starting)return true;
    if(!this.child)return false;
    const status=await this.rpc('/busy',{});
    return !status.ok||status.data?.busy!==false;
  }
  private async start(subject:AnalystAuthority){
    if (!Number.isSafeInteger(subject.actor) || !subject.actor) throw Error('Не подтверждён вход Аналитика.');
    const binding = JSON.stringify([subject.owner, subject.actor, subject.url, createHash('sha256').update(subject.token).digest('hex')]);
    if(this.child&&this.port&&this.authority===binding)return;
    if(this.starting){await this.starting;if(!subject.current())throw Error('Вход Аналитика изменился.');if(this.authority===binding&&this.port)return;}
    if(this.child&&this.port){
      const busy=await this.rpc('/busy',{});
      if(!busy.ok||busy.data?.busy)throw Error('Дождитесь завершения операции Аналитика перед сменой входа.');
    }
    if(!subject.current())throw Error('Вход Аналитика изменился.');
    this.stop();
    const run=async()=>{
      const scopedFolder=join(this.options.home,'analyst',createHash('sha256').update(subject.owner).digest('hex'));
      // Anyone who already worked in the separate CRM profile keeps its journal.
      const existingProfile = subject.existingProfile === true && !existsSync(join(scopedFolder,'backend-settings.json'));
      const folder = existingProfile ? this.options.home : scopedFolder;
      mkdirSync(folder,{recursive:true});
      const source=process.env.HARD_CRM_SOURCE;
      const exe=process.env.HARD_QA_BACKEND||join(process.resourcesPath,'backend','hard-backend.exe');
      if(!source&&!existsSync(exe))throw Error('Не найден движок Аналитика в установленной программе.');
      const processChild=spawn(source?process.env.HARD_CRM_PYTHON||'python':exe,
        source?['-X','utf8','-m','pult.desk_backend','--crm-analyst']:['--crm-analyst'],
        {cwd:source||dirname(exe),windowsHide:true,env:{...process.env,
          PYTHONIOENCODING:'utf-8',HARD_PULT_SETTINGS:join(folder,'backend-settings.json'),HARD_PULT_PROTECT_TOKENS:'1',
          HARD_CLIENT_VERSION:app.getVersion().split('-')[0],HARD_CLIENT_PRODUCT:'analyst'}});
      this.child=processChild;this.authority=binding;
      processChild.stdin.end(JSON.stringify({actor:subject.actor,url:subject.url,token:subject.token,existing_profile:existingProfile,profile_url:subject.profileUrl})+'\n');
      await new Promise<void>((ready,reject)=>{
        let output='',settled=false;const timer=setTimeout(()=>{processChild.kill();fail();},45000);
        const fail=()=>{if(!settled){settled=true;clearTimeout(timer);reject(Error('Движок Аналитика не запустился. Проверьте диагностику.'));}};
        let diagnosticBytes=0;
        processChild.stderr.on('data',chunk=>{if(diagnosticBytes<65536){diagnosticBytes+=chunk.length;appendFileSync(join(folder,'startup.log'),chunk);}});
        processChild.stdout.on('data',chunk=>{
          output=(output+String(chunk)).slice(-4096);const match=/HARD-BACKEND (\d+) (\S+)/.exec(output);
          if(match&&!settled){settled=true;clearTimeout(timer);this.port=Number(match[1]);this.secret=match[2];ready();}
        });
        processChild.on('error',fail);processChild.on('exit',()=>{if(this.child===processChild){this.child=null;this.port=0;this.secret='';}fail();});
      });
    };
    this.starting=run().finally(()=>{this.starting=null;});await this.starting;
  }
  private async rpc(path:string,body:any){
    if(!this.port)throw Error('Движок Аналитика недоступен.');
    try{
      const response=await fetchBuffered(fetch,`http://127.0.0.1:${this.port}${path}`,
        {method:'POST',headers:{'Content-Type':'application/json','X-Local-Secret':this.secret},body:JSON.stringify(body||{})},path==='/admin'?300000:30000);
      return await response.json();
    }catch{return {ok:false,error:'Ответ на операцию потерян. Проверьте прежний номер операции; повторный запуск автоматически не выполняется.'};}
  }
  async request(action:string,args:any[],expected:string):Promise<any>{
    if(!Array.isArray(args)||args.length>5)throw Error('Недопустимый запрос Аналитика.');
    const subject=await this.options.authorize(expected);
    if(this.filesOwner!==subject.owner){this.files.clear();this.filesOwner=subject.owner;}
    const confirm=()=>{if(!subject.current())throw Error('Вход Аналитика изменился.');};
    if(action==='local'){
      const path=scalar(args[0],120),body=args[1]||{};
      if(!localRoutes.has(path)&&!/^\/(?:imp|enr|reg|exp)\/[a-z_-]+$/.test(path))throw Error('Действие Аналитика не разрешено.');
      if(path==='/admin'){
        if(!['GET','POST'].includes(body.method)||typeof body.path!=='string'||!/^\/(?:admin|ops)\/[a-zA-Z0-9_./?-]/.test(body.path)||body.path.includes('..')||body.key)
          throw Error('Недопустимый административный запрос.');
      }
      await this.start(subject);confirm();
      const result=await this.rpc(path,body);confirm();
      if(path==='/state'&&result.data)delete result.data.settings_path;
      return result;
    }
    if(action==='pick'){
      const [kind,opts={}]=args;
      if(!['file','folder','save'].includes(kind))throw Error('Недопустимый выбор файла.');
      const settings={title:typeof opts.title==='string'?opts.title.slice(0,150):undefined,
        filters:Array.isArray(opts.filters)?opts.filters:undefined,defaultPath:typeof opts.defaultPath==='string'?opts.defaultPath:undefined};
      const result=kind==='save'?await dialog.showSaveDialog(this.options.window()!,settings):
        await dialog.showOpenDialog(this.options.window()!,{...settings,properties:[kind==='folder'?'openDirectory':'openFile']});
      confirm();const path=result.canceled?'':('filePath' in result?result.filePath:result.filePaths[0])||'';
      if(path)this.files.add(resolve(path));return path;
    }
    if(action==='copy'){clipboard.writeText(scalar(args[0],4*1024*1024));return true;}
    if(action==='state')return {mode:'analyst',productId:'crm',fixedProduct:true,product:'Аналитик',version:app.getVersion()};
    if(action==='autostart'){
      if(this.options.synthetic())return false;
      if(typeof args[0]==='boolean')app.setLoginItemSettings({openAtLogin:args[0],args:["--tray"]});
      return app.getLoginItemSettings({args:["--tray"]}).openAtLogin || app.getLoginItemSettings().openAtLogin;
    }
    if(action==='saveText'||action==='xlsx'){
      const name=scalar(args[0],180).replace(/[<>:"/\\|?*\x00-\x1f]/g,'_'),excel=action==='xlsx';
      const choice=await dialog.showSaveDialog(this.options.window()!,{defaultPath:excel?name+'.xlsx':name,
        filters:excel?[{name:'Excel',extensions:['xlsx']}]:[{name:'Текст',extensions:['txt']}]});
      if(choice.canceled||!choice.filePath)return {ok:false};confirm();
      let result:any;
      if(excel){
        const headers=args[1],rows=args[2];if(!Array.isArray(headers)||!Array.isArray(rows)||headers.length>500||rows.length>200000)throw Error('Недопустимая таблица.');
        result=await this.options.writeTable({headers,rows},choice.filePath);
      }else{writeFileSync(choice.filePath,scalar(args[1],4*1024*1024),{encoding:'utf-8',flush:true});result={ok:true};}
      if(result.ok)this.files.add(resolve(choice.filePath));return {...result,file:choice.filePath};
    }
    if(action==='open'){
      const path=resolve(scalar(args[0],32000));if(!this.files.has(path)||!['.xlsx','.xlsm','.xlsb','.xls','.txt'].includes(extname(path).toLowerCase()))throw Error('Выберите файл в этом рабочем месте.');
      return shell.openPath(path);
    }
    throw Error('Неизвестное действие Аналитика.');
  }
}
