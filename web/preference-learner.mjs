// Preference evidence remains separate from authored samples and evaluation votes.
const view = {trait: '', seed: null, create: false};
export function seedPreferenceEvidence(seed) { view.seed = seed; view.trait = seed.trait_id || ''; }
export function canTeachComparison(item, kind) {
  return item?.status === 'rated' && ['codex','claude','anthropic'].includes(item.provider)
    && ['a','b'].includes(item.choice) && (kind === 'quality'
      ? item.meaning?.a === 'preserved' && item.meaning?.b === 'preserved'
      : !item.factual_issue);
}
export function canTeachPractice(item) {
  return ['codex','claude','anthropic'].includes(item?.provider) && ['a','b'].includes(item.choice);
}

function node(tag, attributes = {}, ...children) {
  const result = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === 'class') result.className = value;
    else if (key.startsWith('on') && typeof value === 'function') result.addEventListener(key.slice(2), value);
    else if (key in result) result[key] = value;
    else result.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    result.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return result;
}
const help = text => node('p', {class: 'helper'}, text);
const field = (title, control) => node('label', {class: 'field'}, node('span', {class:'field-label'}, title), control);
function options(items, value) {
  return node('select', {}, items.map(([key, title]) => node('option', {value:key, selected:key === value}, title)));
}
function action(title, callback, style = 'small') {
  const button = node('button', {type:'button',class:'button '+style}, title);
  button.addEventListener('click', async () => {button.disabled = true; try {await callback();} finally {button.disabled = false;}});
  return button;
}
const percent = value => Math.round(100 * value);

export function renderPreferenceLearner({state, api, refresh, toast, contextEditor}) {
  const traits = state.preference_traits || [];
  const panel = node('section', {class:'panel preference-learner',id:'preference-learner'});
  panel.append(node('div',{class:'panel-head'},node('div',{},node('h2',{},'How your preferences are developing'),
    help('Strength and confidence stay separate. Keep the evidence, including disagreements, and choose where each preference applies.'))));
  const body = node('div',{class:'panel-body'});
  panel.append(body);
  const fail = error => toast(error.message || String(error), true);
  const run = fn => async () => {try {await fn();} catch(error) {fail(error);}};

  function createForm() {
    const label = node('input',{type:'text',required:true,maxLength:200,placeholder:'e.g. Concrete explanations'});
    const description = node('textarea',{required:true,maxLength:2000,rows:3,placeholder:'The quality to learn more or less of, in your words.'});
    const dimension = options([['diction','Word choice'],['syntax','Sentence construction'],['rhythm','Pacing'],['vernacular','Everyday expressions'],['presentation','Presentation']], 'diction');
    const context = contextEditor(view.seed?.context || {channel:'long-form writing'}, {remember:false});
    const form = node('form',{class:'preference-form'},field('Preference name',label),field('What does this preference mean?',description),field('Aspect',dimension),context.node,
      help('The meaning and scope remain fixed once saved. To learn a different interpretation or scope, create another preference; the earlier evidence remains available.'),
      node('button',{type:'submit',class:'button primary small'},'Create preference'));
    form.addEventListener('submit', async event => {
      event.preventDefault(); if(!form.reportValidity())return;
      const submit=form.querySelector('[type=submit]');submit.disabled=true;
      try {const result=await api('/preferences','POST',{label:label.value.trim(),description:description.value.trim(),dimension:dimension.value,context:context.get()});view.trait=result.trait.id;view.create=false;await refresh();toast('Preference created. Add evidence when you have something useful to teach.');}
      catch(error){fail(error);}finally{submit.disabled=false;}
    });
    return form;
  }

  const creation = node('details',{class:'details',open:!traits.length || view.create},node('summary',{},'Create a preference'),createForm());
  body.append(creation);
  if (!traits.length) body.append(help('No estimates yet. Define one quality, then add a specific correction, preference, or reference. A liked passage can remain a tentative interpretation.'));

  for (const trait of traits) {
    const estimate=trait.estimate;
    const row=node('article',{class:'preference-trait'},node('div',{class:'review-top'},node('h3',{},trait.label),node('span',{class:'badge '+(estimate.disputed?'amber':'muted')},trait.enabled?estimate.confidence_label:'Paused')),
      node('p',{},trait.description),help(Object.entries(trait.context).filter(([,v])=>v).map(([k,v])=>`${k}: ${v}`).join(' · ')),
      node('div',{class:'preference-metrics'},node('span',{},`Strength ${estimate.strength>=0?'+':''}${percent(estimate.strength)}`),node('span',{},`Evidence confidence ${percent(estimate.confidence)}/100`),node('span',{},`${estimate.independent_groups} independent source groups`)),
      help(`${estimate.supporting_groups} supporting · ${estimate.opposing_groups} opposing · ${estimate.pending_evidence} awaiting review`));
    if(estimate.disputed)row.append(help('Conflicting evidence is retained. This preference is not applied until the relevant disagreement is resolved.'));
    if(trait.out_of_scope_evidence)row.append(help(`${trait.out_of_scope_evidence} observations have narrower contexts. Use the situation preview below to inspect their influence there.`));
    const detail=node('div',{});
    row.append(node('div',{class:'button-group'},action('Add evidence',run(async()=>{view.trait=trait.id;view.seed=null;await refresh();document.querySelector('#preference-evidence-form')?.scrollIntoView({block:'center'});})),
      action('Review evidence',run(async()=>{detail.replaceChildren();await loadEvidence(trait.id,detail);})),
      action(trait.enabled?'Pause preference':'Resume preference',run(async()=>{await api(`/preferences/${trait.id}`,'PATCH',{enabled:!trait.enabled});await refresh();}))));
    row.append(detail);body.append(row);
  }

  async function loadEvidence(traitId, host, offset=0) {
    if(offset===0){
      const trait=(await api(`/preferences/${traitId}`)).trait;
      if(trait.history.length)host.append(node('details',{class:'details'},node('summary',{},'Review history'),
        trait.history.map(item=>help(`${item.created_at} · ${item.action} · ${item.reason} · evidence ${item.evidence_id.slice(0,8)}`))));
    }
    const page=await api(`/preferences/${traitId}/evidence?offset=${offset}&limit=20`);
    if(!page.items.length)host.append(help('No evidence recorded yet.'));
    for(const item of page.items){
      const reason=node('input',{type:'text',required:true,maxLength:2000,placeholder:'Reason for reviewing or withdrawing this interpretation'});
      const evidence=node('div',{class:'preference-evidence'},node('strong',{},`${item.source_kind} · ${item.status} · ${item.value>=0?'+':''}${percent(item.value)}`),node('p',{},item.attribution),item.notes?help(item.notes):null,
        help(`Source group ${item.group_key.slice(0,8)} · reliability ${percent(item.reliability)}/100`));
      if(!item.eligible)evidence.append(help(item.inactive_reason));
      if(item.source_url)evidence.append(node('a',{href:item.source_url,target:'_blank',rel:'noopener noreferrer'},'View original source'));
      if(item.before_text || item.after_text || item.reference_text){
        evidence.append(node('details',{class:'details'},node('summary',{},'Source passages, kept separately from authored evidence'),
          item.before_text?node('pre',{class:'provenance-text'},`Before\n${item.before_text}`):null,
          item.after_text?node('pre',{class:'provenance-text'},`After\n${item.after_text}`):null,
          item.reference_text?node('pre',{class:'provenance-text'},item.reference_text):null));
      }
      const buttons=node('div',{class:'button-group'});
      for(const [key,title] of (item.status==='pending'?[['confirm','Confirm this interpretation'],['retract','Dismiss it']]:item.status==='confirmed'?[['retract','Withdraw this evidence']]:item.reviewed_at?[['restore','Restore reviewed evidence']]:[])){
        if(!item.eligible && key!=='retract')continue;
        buttons.append(action(title,run(async()=>{if(!reason.reportValidity())return;await api(`/preference-evidence/${item.id}/${key}`,'POST',{reason:reason.value.trim()});await refresh();toast('Evidence reviewed; the estimate has been recalculated.');})));
      }
      evidence.append(field('Review note',reason),buttons);host.append(evidence);
      if(item.status==='retracted')buttons.append(action('Revise withdrawn interpretation',run(async()=>{
        seedPreferenceEvidence({...item,trait_id:traitId});await refresh();document.querySelector('#preference-evidence-form')?.scrollIntoView({block:'center'});
      })));
    }
    if(page.next_offset!==null){const more=action('Load more evidence',run(async()=>{more.remove();await loadEvidence(traitId,host,page.next_offset);}));host.append(more);}
  }

  if(traits.length && (view.trait || view.seed))body.append(evidenceForm());
  function evidenceForm(){
    const seed=view.seed||{};
    const target=options([['','Choose the preference this evidence relates to'],...traits.filter(x=>x.enabled).map(x=>[x.id,x.label])],view.trait);
    target.required=true;
    const kind=options([['direct','My explicit preference'],['reference','Writing I like'],['edit','An edit I made'],['correction','A saved correction'],['quality','A rated correction comparison'],['evaluation','A rated whole-profile comparison'],['practice','Saved practice feedback']],seed.source_kind||'direct');
    const source=node('div',{});
    const attribution=node('textarea',{required:true,rows:3,maxLength:2000,value:seed.attribution||'',placeholder:'Which aspect do you like or dislike? What does it tell us about this preference?'});
    const initialValue=typeof seed.value==='number'?percent(seed.value):50;
    const value=node('input',{type:'range',min:-100,max:100,step:5,value:initialValue});
    const output=node('output',{},`${initialValue>=0?'Toward':'Away from'} this quality: ${initialValue>=0?'+':''}${initialValue}`);
    value.addEventListener('input',()=>{output.textContent=`${+value.value>=0?'Toward':'Away from'} this quality: ${+value.value>=0?'+':''}${value.value}`;});
    const reliability=options([['1','Clear, specific feedback'],['0.5','A partial supporting clue'],['0.25','A tentative interpretation']],kind.value==='reference'?'0.5':'1');
    const approve=node('input',{type:'checkbox',checked:kind.value==='direct'});
    const contextHost=node('div',{});
    let context=contextEditor(seed.context||traits.find(t=>t.id===view.trait)?.context||{channel:'long-form writing'}, {remember:false});contextHost.append(context.node);
    target.addEventListener('change',()=>{const selected=traits.find(item=>item.id===target.value);if(selected&&!seed.context){context=contextEditor(selected.context,{remember:false});contextHost.replaceChildren(context.node);}});
    let sourceId,reference,before,after,url,style,meaning;
    function sourceFields(){
      source.replaceChildren();sourceId=null;reference=null;before=null;after=null;url=null;style=null;meaning=null;
      if(['correction','quality','evaluation','practice'].includes(kind.value)){
        const rows=kind.value==='correction'?(state.corrections||[]).filter(x=>x.status==='active'):kind.value==='practice'?(state.feedback||[]).filter(canTeachPractice):(state[kind.value==='quality'?'quality_checks':'evaluations']||[]).filter(x=>canTeachComparison(x,kind.value));
        sourceId=options([['','Select a saved source'],...rows.map(x=>[x.id,(x.guidance||x.brief||x.notes||x.chosen_text||x.id).slice(0,100)])],seed.source_id||'');sourceId.required=true;
        const preview=node('div',{});
        const changed=()=>{const selected=rows.find(x=>x.id===sourceId.value);preview.replaceChildren();if(selected){context=contextEditor(selected.context, {remember:false});contextHost.replaceChildren(context.node);
          const texts=kind.value==='correction'?[['Before',selected.original_text],['After',selected.revised_text]]:(selected.options||[]).map(item=>[`${item.id.toUpperCase()}${selected.choice===item.id?' · preferred':''}`,item.text]);
          if(selected.edited_text)texts.push(['Your feedback edit',selected.edited_text]);
          preview.append(node('details',{class:'details'},node('summary',{},'Review this source'),texts.map(([title,text])=>node('pre',{class:'provenance-text'},`${title}\n${text}`))));
        }};
        sourceId.addEventListener('change',changed);changed();
        source.append(field('Saved source',sourceId),preview,help('A vote alone does not identify which aspect you preferred. Explain the attribution below. Whole-profile comparisons receive less evidence weight because several things changed.'));
        if(kind.value==='practice'){
          style=node('input',{type:'checkbox'});meaning=node('input',{type:'checkbox'});
          source.append(field('This comparison teaches style',style),field('The meaning and facts are preserved',meaning));
        }
      }else if(kind.value==='reference'){
        const saved=(state.samples||[]).filter(item=>!item.held_out);
        sourceId=options([['','Paste a passage below'],...saved.map(item=>[item.id,item.title.slice(0,110)])],seed.source_id||'');
        reference=node('textarea',{rows:5,maxLength:4000,required:true,value:seed.reference_text||'',placeholder:'Paste the passage. This will not become writing attributed to you.'});
        url=node('input',{type:'url',maxLength:4096,value:seed.source_url||'',placeholder:'https://…'});
        const savedChanged=()=>{const selected=saved.find(item=>item.id===sourceId.value);reference.required=!selected;reference.disabled=Boolean(selected);url.disabled=Boolean(selected);url.value=selected?.source_url||'';reference.placeholder=selected?'The saved passage is linked by ID. Its text and authorship remain in Samples.':'Paste the passage. This will not become writing attributed to you.';};
        sourceId.addEventListener('change',savedChanged);savedChanged();
        source.append(field('Saved passage (authorship stays unchanged)',sourceId),field('Passage you like',reference),field('Source URL or video link',url),help('References from the same source share a capped contribution. Liking a passage does not approve every feature, claim, or experience in it.'));
      }else if(kind.value==='edit'){
        before=node('textarea',{rows:4,maxLength:30000,required:true,value:seed.before_text||''});
        after=node('textarea',{rows:4,maxLength:30000,required:true,value:seed.after_text||''});
        style=node('input',{type:'checkbox'});meaning=node('input',{type:'checkbox'});
        source.append(field('Original wording',before),field('Your revised wording',after),field('This change teaches style',style),field('The meaning and facts are preserved',meaning));
      }
    }
    kind.addEventListener('change',()=>{approve.checked=kind.value==='direct';reliability.value=kind.value==='reference'?'0.5':'1';sourceFields();});sourceFields();
    const form=node('form',{class:'preference-form',id:'preference-evidence-form'},node('h3',{},'What does this example tell us?'),field('Preference',target),field('Evidence type',kind),source,
      field('Your interpretation',attribution),field('How strongly does it point toward or away from this quality?',value),output,field('How clear is that interpretation?',reliability),contextHost,
      field('I reviewed this interpretation; use it where relevant',approve),help('Leave unchecked to save a proposal for later review. Strength is your judgment; confidence is computed from the reviewed evidence.'),
      node('div',{class:'button-group'},node('button',{type:'submit',class:'button primary small'},'Save evidence'),action('Cancel',run(async()=>{view.trait='';view.seed=null;await refresh();}))));
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!form.reportValidity())return;const submit=form.querySelector('[type=submit]');submit.disabled=true;
      try{const packet={source_kind:kind.value,value:+value.value/100,reliability:+reliability.value,attribution:attribution.value.trim(),context:context.get(),confirmed:approve.checked};
        if(sourceId)packet.source_id=sourceId.value;
        if(reference&&!sourceId.value){packet.reference_text=reference.value;packet.source_url=url.value.trim();}
        if(before){packet.before_text=before.value;packet.after_text=after.value;}
        if(style){packet.style_only=style.checked;packet.meaning_preserved=meaning.checked;}
        const result=await api(`/preferences/${target.value}/evidence`,'POST',packet);view.trait='';view.seed=null;await refresh();toast(result.evidence.status==='pending'?'Interpretation saved for review. It does not affect drafts yet.':'Evidence saved; the preference estimate has been updated.');
      }catch(error){fail(error);}finally{submit.disabled=false;}
    });
    return form;
  }

  if(traits.length){
    const context=contextEditor({channel:'long-form writing'}, {remember:false});const output=node('div',{});
    body.append(node('details',{class:'details'},node('summary',{},'Preview which preferences apply to a situation'),context.node,action('Show applicable preferences',run(async()=>{
      const result=await api('/preferences/resolve','POST',{context:context.get()});output.replaceChildren(...result.preferences.map(item=>node('p',{},`${item.label}: strength ${percent(item.estimate.strength)}, evidence confidence ${percent(item.estimate.confidence)}/100`)),...result.warnings.map(help));if(!result.preferences.length)output.prepend(help('No reviewed preference has enough consistent evidence to apply in this situation.'));
    })),output));
  }
  body.append(help('Evidence confidence is a transparent index of independent support and consistency. It is not a probability that the next draft sounds like you. Direct instructions and your existing explicit lessons remain primary.'));
  return panel;
}
