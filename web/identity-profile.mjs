// Communication goals are chosen and reviewed, never a personality score.
const view = {profile: '', seed: null, detail: null, job: null, polling: false, analysis: null};
const labels = {goal:'What outcome do you want?', presence:'How do you want to come across?', long_term_goals:'Longer-term goals, when relevant', boundaries:'What should this never trade away?'};
export const identityFields = Object.keys(labels);
export function seedIdentitySource(seed) { view.seed = {...seed, kind:seed.kind || seed.source_kind}; delete view.seed.source_kind; view.profile = ''; view.detail = null; }
export function identityReviewable(proposal) { return proposal.status === 'pending' && !proposal.stale && !proposal.source_issues?.length; }
export function identitySourcePacket(seed) {
  const allowed = ['kind','source_id','text','before_text','after_text','notes','title','source_ref','observed_at','collector','source_group','source_nature'];
  return Object.fromEntries(allowed.filter(key => seed[key] !== undefined).map(key => [key,seed[key]]));
}
function node(tag, attrs={}, ...children) {
  const n=document.createElement(tag);
  for(const [key,value] of Object.entries(attrs)) {
    if(key==='class') n.className=value;
    else if(key.startsWith('on') && typeof value==='function')n.addEventListener(key.slice(2),value);
    else if(key in n)n[key]=value;
    else n.setAttribute(key,value);
  }
  for(const c of children.flat(Infinity))if(c!==null && c!==undefined && c!==false)n.append(c instanceof Node?c:document.createTextNode(String(c)));
  return n;
}
const help=text=>node('p',{class:'helper'},text);
const field=(label,control)=>node('label',{class:'field'},node('span',{class:'field-label'},label),control);
const area=(value='',rows=3)=>node('textarea',{value,rows,maxLength:2000});

export function renderIdentityProfiles({state,api,refresh,toast,contextEditor}) {
  const profiles=state.identity_profiles || [];
  const panel=node('section',{class:'panel identity-layer',id:'identity-layer'},node('div',{class:'panel-head'},node('div',{},
    node('h2',{},'Identity and goals, in context'),help('Choose the outcome and presence you want in a situation. Edits, comparisons and supplied context can suggest changes for you to review.'))));
  const body=node('div',{class:'panel-body'});panel.append(body);
  const fail=error=>toast(error.message || String(error),true);
  const action=(text,fn,style='small')=>{
    const b=node('button',{type:'button',class:'button '+style},text);
    b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(e){fail(e);}finally{b.disabled=false;}});return b;
  };
  const reload=async id=>{view.profile=id;view.detail=await api('/identities/'+id);await refresh();};
  if(view.profile && !profiles.some(p=>p.id===view.profile)) {view.profile='';view.detail=null;}
  if(!view.profile && !view.seed && profiles.length===1)view.profile=profiles[0].id;

  const createContext=contextEditor(view.seed?.context || {channel:'work'},{remember:false});
  const name=node('input',{value:view.seed?`${view.seed.context?.channel || 'Writing'} · working presence`:'',maxLength:200,required:true,placeholder:'e.g. Team updates, public essays, or close friends'});
  const create=node('form',{class:'identity-form'},field('Profile name',name),createContext.node,
    help('Start with an empty intake and let AI interpret your context, or enter your goals after creating the profile.'),
    node('button',{type:'submit',class:'button primary small'},'Create this context'));
  create.addEventListener('submit',async event=>{
    event.preventDefault();if(!create.reportValidity())return;
    const b=create.querySelector('[type=submit]');b.disabled=true;
    try {const result=await api('/identities','POST',{label:name.value.trim(),context:createContext.get()});await reload(result.profile.id);}
    catch(e){fail(e);}finally{b.disabled=false;}
  });
  body.append(node('details',{class:'details',open:!profiles.length || Boolean(view.seed && !view.profile)},node('summary',{},'Create a context-specific profile'),create));
  if(!profiles.length){body.append(help('Your writing style and your goals are separate. An empty profile applies nothing until you save your own goals or accept an inference.'));return panel;}
  const select=node('select',{'aria-label':'Identity profile'},node('option',{value:''},'Choose a profile'),profiles.map(p=>node('option',{value:p.id,selected:p.id===view.profile},p.label+(p.active?' · in use':' · paused or draft'))));
  select.addEventListener('change',async()=>{try{view.profile=select.value;view.detail=null;await refresh();}catch(e){fail(e);}});
  body.append(field('Profile to review or teach',select));
  const profile=profiles.find(p=>p.id===view.profile);
  if(!profile)return panel;
  body.append(help(Object.entries(profile.context).filter(([,v])=>v).map(([k,v])=>`${k}: ${v}`).join(' · ')));
  const inputs=Object.fromEntries(identityFields.map(key=>[key,area(profile.values[key])]));
  const editor=node('form',{class:'identity-form'},identityFields.map(key=>field(labels[key],inputs[key])),
    help('These are communication goals, not factual claims or a psychological assessment. Current instructions and your explicit voice preferences take priority.'),
    node('div',{class:'button-group'},node('button',{type:'submit',class:'button small primary'},'Save my goals'),
      action(profile.active?'Pause profile':'Use this profile',async()=>{await api('/identities/'+profile.id,'PATCH',{revision:profile.revision,active:!profile.active});await reload(profile.id);})));
  editor.addEventListener('submit',async event=>{
    event.preventDefault();const b=editor.querySelector('[type=submit]');b.disabled=true;
    try {const values=Object.fromEntries(identityFields.map(k=>[k,inputs[k].value.trim()]));await api('/identities/'+profile.id,'PATCH',{revision:profile.revision,values,active:Object.values(values).some(Boolean)});await reload(profile.id);toast('Your goals were saved for this context.');}
    catch(e){fail(e);}finally{b.disabled=false;}
  });
  body.append(node('details',{class:'details',open:!Object.values(profile.values).some(Boolean)},node('summary',{},'Your current goals and presence'),editor));

  const sourceText=node('textarea',{rows:5,maxLength:24000,placeholder:'Paste relevant original context or your feedback. The collecting AI does not need to interpret it.'});
  const sourceTitle=node('input',{maxLength:200,placeholder:'Where is this from?'});
  const sourceRef=node('input',{maxLength:2000,placeholder:'Document link, message reference, or other source reference'});
  const sourceKind=node('select',{},node('option',{value:'context'},'Source context'),node('option',{value:'feedback'},'My feedback'));
  const sourceNature=node('select',{},node('option',{value:'unknown'},'Unknown or mixed authorship'),node('option',{value:'original'},'Original source text'),node('option',{value:'assistant_summary'},'Assistant summary or interpretation'));
  const sourceFile=node('input',{type:'file',accept:'.txt,.md,.json','aria-label':'Open a selected context file'});
  sourceFile.addEventListener('change',async()=>{
    const file=sourceFile.files?.[0];if(!file)return;
    try {if(file.size>100000)throw new Error('Choose a smaller context file. A source accepts up to 24,000 characters.');const text=await file.text();if(text.length>24000)throw new Error('This file exceeds 24,000 characters. Select a relevant passage; nothing was truncated.');sourceText.value=text;sourceTitle.value=file.name;sourceRef.value='User-selected context file: '+file.name;}
    catch(e){fail(e);}finally{sourceFile.value='';}
  });
  const sourceForm=node('form',{class:'identity-form'},view.seed&&help('Selected source ready: '+view.seed.kind+'. It will be saved as context, not as authored writing or an approved identity.'),
    !view.seed&&field('Source type',sourceKind),!view.seed&&field('Material supplied',sourceNature),!view.seed&&field('Open a selected context file · optional',sourceFile),!view.seed&&field('Relevant source material',sourceText),
    !view.seed&&field('Source name',sourceTitle),!view.seed&&field('Source reference',sourceRef),
    node('button',{type:'submit',class:'button primary small'},view.seed?'Add selected source':'Add context for AI to interpret'));
  sourceForm.addEventListener('submit',async event=>{
    event.preventDefault();const b=sourceForm.querySelector('[type=submit]');b.disabled=true;
    try {const packet=view.seed?identitySourcePacket(view.seed):{kind:sourceKind.value,source_nature:sourceNature.value,text:sourceText.value,title:sourceTitle.value||'User-supplied context',source_ref:sourceRef.value};
      await api('/identities/'+profile.id+'/sources','POST',packet);view.seed=null;await reload(profile.id);toast('Context saved. Choose the sources to interpret below.');}
    catch(e){fail(e);}finally{b.disabled=false;}
  });
  body.append(node('details',{class:'details',open:Boolean(view.seed)},node('summary',{},'Teach from context or feedback'),sourceForm));

  const detail=node('div',{class:'identity-review'});body.append(detail);
  async function showDetail(data) {
    detail.replaceChildren();
    const selected=[];
    const sources=data.sources || [];
    const eligibleCount=sources.filter(s=>s.eligible).length;
    const sourceBody=node('div',{class:'identity-source-list'});
    detail.append(node('details',{class:'details',open:!(data.proposals||[]).some(p=>p.status==='pending')},node('summary',{},'Context for the AI · '+sources.length+' saved sources'),sourceBody));
    if(!sources.length)sourceBody.append(help('Add a source, or use an edit or rated comparison’s identity-learning action. A connected assistant can also submit context through Voicework’s MCP tools.'));
    for(const source of sources) {
      const checkbox=node('input',{type:'checkbox',checked:source.eligible && eligibleCount<=12,disabled:!source.eligible});
      selected.push({source,checkbox});
      const row=node('div',{class:'identity-source'},node('label',{class:'check'},checkbox,node('span',{},source.title+' · '+source.kind)),
        help((source.source_ref || 'No external source reference')+' · '+(source.observed_at || 'Source date unknown')+' · '+(source.source_nature || 'unknown')+' · '+(source.eligible?'Unverified source material':source.inactive_reason)),
        action('Inspect source',async()=>{const raw=(await api('/identities/'+profile.id+'/sources')).sources.find(s=>s.id===source.id);sourceBox.replaceChildren(node('pre',{class:'identity-source-text'},JSON.stringify(raw?.payload,null,2)));}),
        action(source.withdrawn?'Restore source':'Withdraw source',async()=>{await api('/identity-sources/'+source.id,'PATCH',{withdrawn:!source.withdrawn});await reload(profile.id);}));
      const sourceBox=node('div',{});row.append(sourceBox);sourceBody.append(row);
    }
    if(sources.length){const infer=action('Let Voicework interpret selected context',async()=>{
      const source_ids=selected.filter(x=>x.checkbox.checked).map(x=>x.source.id);
      if(!source_ids.length)throw new Error('Select at least one source.');
      if(source_ids.length>12)throw new Error('Select at most twelve related sources for this pass.');
      view.analysis=null;const job=await api('/identities/'+profile.id+'/infer','POST',{source_ids});view.job={id:job.id,profile:profile.id};
      try{sessionStorage.setItem('voicework-identity-job',JSON.stringify(view.job));}catch{}
      await refresh();
    },'primary small');
      const selection=help('');
      const updateSelection=()=>{const count=selected.filter(x=>x.checkbox.checked).length;selection.textContent=count+' sources selected · twelve maximum per pass. All supplied sources stay saved.';infer.disabled=!state.settings?.live || count===0 || count>12 || view.job?.profile===profile.id;};
      selected.forEach(x=>x.checkbox.addEventListener('change',updateSelection));updateSelection();
      sourceBody.append(selection,infer);if(!state.settings?.live)sourceBody.append(help('Select a live provider to interpret context. Offline rehearsal keeps your context saved.'));}
    detail.append(help('Voicework uses its configured model to interpret this context. The collecting assistant only supplies material. Inferences need source quotes and remain pending until you review them.'));
    for(const proposal of data.proposals || []) {
      const row=node('article',{class:'identity-proposal'},node('div',{class:'review-top'},node('h3',{},proposal.status==='no_change'?'Context analysis · no update':'Suggested profile update'),node('span',{class:'badge muted'},proposal.status+(proposal.stale?' · outdated':''))),
        help(proposal.analysis.rationale),help('Analyzed by '+(proposal.analysis.provider || 'the configured model')));
      const edits={};
      for(const [key,value] of Object.entries(proposal.status==='accepted'?(proposal.analysis.reviewed_changes||proposal.changes):proposal.changes)) {
        const enabled=node('input',{type:'checkbox',checked:true});const text=area(value);
        edits[key]={enabled,text};
        row.append(node('div',{class:'identity-change'},node('label',{class:'check'},enabled,node('strong',{},labels[key])),
          help('Current: '+(profile.values[key]||'Unspecified')),field('Proposed wording · editable',text),
          (proposal.analysis.support[key]||[]).map(s=>node('blockquote',{},node('p',{},s.quote),help(s.rationale+' · Source '+s.source_id)))));
      }
      for(const key of ['uncertainties','conflicts','questions'])if(proposal.analysis[key]?.length)row.append(node('details',{class:'details',open:key==='conflicts'},node('summary',{},key.charAt(0).toUpperCase()+key.slice(1)),proposal.analysis[key].map(text=>help(text))));
      if(proposal.status==='pending') {
        const accept=action('Use selected interpretations',async()=>{
          const changes=Object.fromEntries(Object.entries(edits).filter(([,v])=>v.enabled.checked).map(([key,v])=>[key,v.text.value.trim()]));
          if(!Object.keys(changes).length)throw new Error('Select at least one proposed field, or reject this interpretation.');
          await api('/identity-proposals/'+proposal.id,'POST',{action:'accept',changes,reason:'Reviewed selected fields in Voicework'});await reload(profile.id);
        },'primary small');accept.disabled=!identityReviewable(proposal);
        row.append(node('div',{class:'button-group'},accept,action('Reject interpretation',async()=>{await api('/identity-proposals/'+proposal.id,'POST',{action:'reject',reason:'Rejected in Voicework'});await reload(profile.id);})));
        if(!identityReviewable(proposal))row.append(help('The profile or source changed. Run a fresh inference before accepting.'));
      } else if(proposal.status==='accepted')row.append(action('Undo this acceptance',async()=>{await api('/identity-proposals/'+proposal.id,'POST',{action:'undo',reason:'Undone in Voicework'});await reload(profile.id);}));
      detail.append(row);
    }
    if(view.analysis?.profile===profile.id)detail.append(node('details',{class:'details',open:true},node('summary',{},'Why no update was proposed'),help(view.analysis.value.rationale),(view.analysis.value.questions||[]).map(text=>help(text)),(view.analysis.value.uncertainties||[]).map(text=>help(text))));
    if(data.history?.length)detail.append(node('details',{class:'details'},node('summary',{},'Profile review history'),data.history.map(h=>help(h.created_at+' · '+h.action+' · '+h.reason))));
  }
  if(view.detail?.profile.id===profile.id)showDetail(view.detail).catch(fail);
  else api('/identities/'+profile.id).then(data=>{if(view.profile===profile.id){view.detail=data;return showDetail(data);}}).catch(fail);
  if(!view.job)try{view.job=JSON.parse(sessionStorage.getItem('voicework-identity-job')||'null');}catch{}
  if(view.job?.profile===profile.id) {
    body.append(help('Voicework is interpreting the selected context. The current profile stays unchanged until review.'));
    if(!view.polling) {
      view.polling=true;
      const poll=async()=>{
        try {
          const job=await api('/identity-jobs/'+view.job.id);
          if(job.status==='pending'||job.status==='running'){setTimeout(poll,1200);return;}
          view.polling=false;view.job=null;try{sessionStorage.removeItem('voicework-identity-job');}catch{}
          if(job.status==='failed')throw new Error(job.error||'Identity inference failed. Your context is still saved.');
          view.analysis=job.result?.proposal?null:{profile:profile.id,value:job.result?.analysis||{}};
          await reload(profile.id);
          toast(job.result?.proposal?'An interpretation is ready for review.':'The AI did not propose a change. Review its questions before adding more context.');
          if(!job.result?.proposal){detail.append(node('details',{class:'details',open:true},node('summary',{},'Why no update was proposed'),help(job.result?.analysis?.rationale||''),(job.result?.analysis?.questions||[]).map(text=>help(text))));}
        }catch(e){view.polling=false;view.job=null;try{sessionStorage.removeItem('voicework-identity-job');}catch{}fail(e);}
      };
      setTimeout(poll,300);
    }
  }
  return panel;
}
