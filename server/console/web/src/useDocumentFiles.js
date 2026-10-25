import {useEffect,useState} from 'react';

export function useDocumentFiles(session, readKey, api) {
  const [state,setState] = useState({session:null,files:[],loading:false,error:''});
  useEffect(()=>{
    if (!session || readKey==='[]' || !api) return;
    let active=true,timer;
    const controller=new AbortController();
    setState(old=>({session,files:old.session===session?old.files:[],loading:true,error:''}));
    async function poll() {
      let delay=10000;
      try {
        const value=await api('/api/documents/'+encodeURIComponent(session),undefined,{signal:controller.signal});
        if (!active) return;
        setState(old=>({session,files:Array.isArray(value.files)?value.files:old.files,loading:!!value.loading,error:value.sync?.error||''}));
        if(value.loading||value.sync?.refreshing) delay=750;
        if(value.sync?.error) delay=4000;
      } catch(error) {
        if(!active) return;
        setState(old=>({...old,loading:false,error:error.message}));delay=4000;
      }
      if(active) timer=setTimeout(poll,delay);
    }
    poll();return()=>{active=false;controller.abort();clearTimeout(timer);};
  },[session,readKey,api]);
  return state.session===session?state:{files:[],loading:readKey!=='[]',error:''};
}
