import './browserCompat.js';

// A rejected module must leave a working recovery screen, never a silent splash.
import('./main.jsx').catch(error=>{
 window.dispatchEvent(new CustomEvent('console-startup-failed',{detail:{
  code:error?.name==='SyntaxError'?'startup_syntax_error':'startup_module_failed'
 }}));
});
