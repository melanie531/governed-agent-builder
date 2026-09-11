import {useEffect, useState} from 'react';
import {Alert, Box, Button, Container, ContentLayout, FormField, Header, Input, SpaceBetween} from '@cloudscape-design/components';

export type Verification = {state:'PENDING_EMAIL_VERIFICATION';csrf:string;expires:number;next_send:number;sends_remaining:number};

export default function VerifyEmail({initial,onComplete}:{initial:Verification;onComplete:()=>Promise<void>}){
 const [status,setStatus]=useState(initial),[code,setCode]=useState(''),[now,setNow]=useState(Date.now()/1000),[busy,setBusy]=useState(false),[error,setError]=useState(''),[sent,setSent]=useState(false);
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()/1000),1000);return()=>clearInterval(timer);},[]);
 const remaining=Math.max(0,Math.ceil(status.expires-now)),cooldown=Math.max(0,Math.ceil(status.next_send-now));
 async function submit(action:'send'|'verify'){
  setBusy(true);setError('');if(action==='send')setSent(false);
  try{
   const response=await fetch('/auth/verification/'+action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':status.csrf},body:JSON.stringify(action==='verify'?{code}:{})});
   const body=await response.json();
   if(!response.ok){if(response.status===401)setStatus(s=>({...s,expires:0}));throw new Error(body.detail||'Verification could not be completed');}
   if(action==='send'){if(body.sent!==true)throw new Error('Email delivery could not be confirmed');setStatus(body);setSent(true);}
   else{setCode('');await onComplete();}
  }catch(e){setError((e as Error).message);
   // Failed provider sends still spend a server-side budget; refresh cooldown.
   if(action==='send')try{const r=await fetch('/auth/verification/status',{headers:{'X-Studio-Verification':'1'},credentials:'same-origin'});if(r.ok)setStatus(await r.json());}catch{/* Keep the existing expiry if offline. */}
  }finally{setBusy(false);}
 }
 return <ContentLayout header={<Header variant="h1">Verify email</Header>}><Container><SpaceBetween size="l">
  <Box>Verify your email before opening your workspace. Send a code to the email address associated with your sign-in, then enter it here.</Box>
  {remaining>0?<Box>Verification expires in {remaining} seconds.</Box>:<Alert type="warning">Verification expired. Start sign-in again.</Alert>}
  {error&&<Alert type="error">{error}</Alert>}
  {sent&&remaining>0&&<Alert type="success">Verification code sent. Check your email.</Alert>}
  <Button disabled={busy||remaining===0||cooldown>0||status.sends_remaining===0} onClick={()=>void submit('send')}>{status.sends_remaining===3?'Send verification code':'Resend verification code'}</Button>
  {cooldown>0&&remaining>0&&<Box>Resend available in {cooldown} seconds.</Box>}
  {status.sends_remaining===0&&<Box>Send limit reached. Start sign-in again when this verification expires.</Box>}
  <FormField label="Verification code" constraintText="Enter the six-digit code from your email."><Input ariaLabel="Verification code" value={code} autoComplete="one-time-code" disabled={busy||remaining===0} onChange={({detail})=>setCode(detail.value.slice(0,6))}/></FormField>
  <Button variant="primary" loading={busy} disabled={remaining===0||!/^\d{6}$/.test(code)} onClick={()=>void submit('verify')}>Verify email and open Studio</Button>
  <Button href="/auth/login">Start sign-in again</Button>
 </SpaceBetween></Container></ContentLayout>;
}
