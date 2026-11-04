// Keep the console shell usable on older Android Chromium browsers.
// These small standard helpers are also used by deferred transcript modules.
const lengthOf=value=>Math.min(Math.max(Math.floor(Number(value)||0),0),Number.MAX_SAFE_INTEGER);
if(!Array.prototype.at)Object.defineProperty(Array.prototype,'at',{configurable:true,writable:true,value:function(index){
 if(this==null)throw TypeError('Array expected');
 const object=Object(this),length=lengthOf(object.length);
 let position=Math.trunc(Number(index)||0);if(position<0)position+=length;
 return position<0||position>=length?undefined:object[position];
}});
if(!Array.prototype.findLastIndex)Object.defineProperty(Array.prototype,'findLastIndex',{configurable:true,writable:true,value:function(predicate,thisArg){
 if(this==null||typeof predicate!=='function')throw TypeError('Array and predicate expected');
 const object=Object(this);
 for(let i=lengthOf(object.length)-1;i>=0;i--)if(predicate.call(thisArg,object[i],i,object))return i;
 return -1;
}});
if(!Object.hasOwn)Object.defineProperty(Object,'hasOwn',{configurable:true,writable:true,value:(object,key)=>Object.prototype.hasOwnProperty.call(object,key)});
