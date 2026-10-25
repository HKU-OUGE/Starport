import {replayArtifact} from './src/artifactReplay.js';
let raw='';for await(const part of process.stdin){raw+=part;if(raw.length>24*1024*1024)throw Error('replay_limit');}
try{const {value,images}=JSON.parse(raw);process.stdout.write(JSON.stringify({ok:true,value:replayArtifact(value,images)}));}
catch(e){process.stdout.write(JSON.stringify({ok:false,error:String(e.message).slice(0,150)}));}
