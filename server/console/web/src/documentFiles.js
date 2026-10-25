const linkPattern = /!?\[[^\]\n]*\]\((?:<([^<>\n]+)>|([^\s()]+))(?:\s+"[^"\n]*")?\)/g;

export function localPdf(value) {
  if (typeof value !== 'string' || value.length > 4096 || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || value.includes('\0')) return false;
  try { return decodeURIComponent(value.split('#')[0]).toLowerCase().endsWith('.pdf'); }
  catch { return false; }
}

export function documentReferences(event) {
  if (event.role === 'tool' && (event.tool_name || event.text || '').split('__').at(-1) === 'SendUserFile') {
    return Array.isArray(event.input?.files) ? event.input.files.filter(localPdf).slice(0,24) : [];
  }
  if (event.role !== 'assistant') return [];
  return [...(event.text || '').matchAll(linkPattern)].map(m=>m[1]||m[2]).filter(localPdf);
}

export function attachDocuments(events, files, pending=false, error='') {
  const byReference = new Map(files.map(file=>[file.reference,file]));
  return events.map(event=>{
    const refs = documentReferences(event);
    if (!refs.length) return event;
    const attachments = [...(event.attachments || [])];
    for (const reference of refs) {
      const item = byReference.get(reference) || {reference,name:decodeURIComponent(reference.split('#')[0]).split('/').at(-1),mime:'application/pdf',source:'document',available:false,pending,error:error||'document_file_unavailable'};
      const index=attachments.findIndex(old=>old.reference===reference || item.id&&old.id===item.id);
      if(index<0)attachments.push(item);
      else if(item.available||!attachments[index].available)attachments[index]=item;
    }
    return {...event,attachments};
  });
}

export function registeredReference(urls,value) {
  if(urls.has(value))return urls.get(value);
  try{return urls.get(decodeURIComponent(value));}catch{return undefined;}
}

export function documentReadKey(events) {
  return JSON.stringify([...new Set(events.flatMap(documentReferences))].sort());
}
