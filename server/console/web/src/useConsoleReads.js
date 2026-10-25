import {useState, useEffect, useRef, useCallback} from 'react';
import {applyDelta, SessionStore} from './sessionStore.js';

// List, health and selected-session reads have separate cancellation scopes.
// Nothing here retries or aborts a user mutation.
export function useConsoleReads(auth, selected, api) {
  const [sessions, setSessions] = useState([]), [detail, setDetail] = useState(null);
  const [status, setStatus] = useState(null), [loading, setLoading] = useState(true);
  const [detailSync, setDetailSync] = useState({}), [listError, setListError] = useState('');
  const [revision, setRevision] = useState(0);
  const cache = useRef(new SessionStore());
  const refresh = useCallback(() => setRevision(n => n + 1), []);

  useEffect(() => {
    if (!auth) {cache.current.clear(); setSessions([]); setDetail(null); setStatus(null); setLoading(true); setDetailSync({}); setListError('');}
  }, [auth]);

  useEffect(() => {
    if (!auth) return;
    let active = true, timer;
    const controller = new AbortController();
    async function poll() {
      let delay = 10000;
      try {
        const value = await api('/api/sessions?background=1', undefined, {signal: controller.signal});
        if (!active) return;
        if (Array.isArray(value.sessions)) {setSessions(value.sessions); setLoading(false);}
        if (value.loading || value.sync?.restored) delay = 750;
        if (value.sync?.error) delay = 4000;
        setListError(value.sync?.error || '');
      } catch (error) {if (active) {setListError(error.message); delay = 4000;}}
      finally {if (active) timer = setTimeout(poll, delay);}
    }
    poll();
    return () => {active = false; controller.abort(); clearTimeout(timer);};
  }, [auth, revision, api]);

  useEffect(() => {
    if (!auth) return;
    let active = true, timer;
    const controller = new AbortController();
    async function poll() {
      let delay = 15000;
      try {
        const value = await api('/api/status?background=1', undefined, {signal: controller.signal});
        if (!active) return;
        if (!value.loading) setStatus(value);
        if (value.loading || value.sync?.restored) delay = 750;
        if (value.sync?.error) {setStatus(old => ({...old, ok: false})); delay = 4000;}
      } catch {if (active) {setStatus(old => ({...old, ok: false})); delay = 4000;}}
      finally {if (active) timer = setTimeout(poll, delay);}
    }
    poll();
    return () => {active = false; controller.abort(); clearTimeout(timer);};
  }, [auth, api]);

  useEffect(() => {
    if (!auth || !selected) {setDetail(null); return;}
    let active = true, timer, snapshot = cache.current.get(selected);
    const controller = new AbortController();
    setDetail(snapshot); setDetailSync({});
    async function poll() {
      let delay = 4000;
      try {
        const read = cursor => api('/api/session/' + selected + '?delta=1' + (cursor ? '&cursor=' + cursor : ''), undefined, {signal: controller.signal});
        let value = await read(snapshot?._cursor);
        if (!active) return;
        if (!value.loading) {
          let next;
          try {next = applyDelta(snapshot, value, selected);}
          catch {
            value = await read(null);
            if (!active) return;
            if (!value.loading) next = applyDelta(null, value, selected);
          }
          if (next) {snapshot = next; cache.current.set(selected, next); setDetail(next);}
        }
        setDetailSync(value.sync || {});
        if (value.loading || value.sync?.restored) delay = 750;
        if (value.sync?.error) delay = 4000;
      } catch (error) {if (active) setDetailSync({error: error.message});}
      finally {if (active) timer = setTimeout(poll, delay);}
    }
    poll();
    return () => {active = false; controller.abort(); clearTimeout(timer);};
  }, [auth, selected, revision, api]);
  return {sessions, detail, detailSync, status, loading, listError, refresh};
}
