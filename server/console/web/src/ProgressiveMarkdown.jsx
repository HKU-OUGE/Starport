import React,{useEffect,useSyncExternalStore} from 'react';
import {BasicMarkdown} from './BasicMarkdown.js';
import {richText} from './richTextLoader.js';

class RendererBoundary extends React.Component{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true};}
 componentDidCatch(){richText.renderFailed();}
 render(){return this.state.failed?this.props.fallback:this.props.children;}
}
export function Markdown(props){
 const state=useSyncExternalStore(richText.subscribe,richText.getSnapshot,richText.getSnapshot);
 useEffect(()=>{richText.load();},[]);
 const fallback=<BasicMarkdown {...props}/>;
 if(!state.component)return fallback;
 const Rich=state.component;
 return <RendererBoundary key={state.version} fallback={fallback}><Rich {...props}/></RendererBoundary>;
}
export function MarkdownStatus(){
 const state=useSyncExternalStore(richText.subscribe,richText.getSnapshot,richText.getSnapshot);
 if(!['slow','error'].includes(state.phase))return null;
 return <div className="format-status" role="status"><span>{state.phase==='slow'?'正文已显示，完整排版仍在后台加载。':'正文、工具和附件可正常使用，完整排版暂未加载。'}</span>{state.phase==='error'&&<button onClick={()=>richText.load()}>重试排版</button>}</div>;
}
export const preloadTranscript=()=>richText.load();
