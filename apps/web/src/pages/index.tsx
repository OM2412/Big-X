import { useState } from "react";
import Head from "next/head";
import { useAuth } from "../hooks";
import Landing from "../components/Landing";
import { Icon, Mark } from "../components/Identity";
import Dashboard from "../components/Dashboard";
import Marketplace from "../components/Marketplace";
import AgentStudio from "../components/AgentStudio";
import ChatInterface from "../components/ChatInterface";
import SmartContractIntegration from "../components/SmartContractIntegration";
import PaymentFlow from "../components/PaymentFlow";
import Settings from "../components/Settings";
const tabs = [
 {id:"overview",name:"Overview",icon:"grid",subtitle:"Your agent economy, at a glance."},
 {id:"marketplace",name:"Marketplace",icon:"compass",subtitle:"Discover intelligence. Make it yours."},
 {id:"studio",name:"Agent Studio",icon:"spark",subtitle:"Give your next idea an identity of its own."},
 {id:"chat",name:"Agent Chat",icon:"chat",subtitle:"Put your agents to work."},
 {id:"activity",name:"On-chain Activity",icon:"layers",subtitle:"Follow the ownership. See the record."},
 {id:"payments",name:"Payments",icon:"wallet",subtitle:"Create and track payments for your agents."},
 {id:"settings",name:"Settings",icon:"settings",subtitle:"Your wallet, network, and workspace."},
];
export default function Home() {
 const {token,address,loading,login,signOut,error}=useAuth();
 const [active,setActive]=useState("overview");
 const [menuOpen,setMenuOpen]=useState(false);
 const current=tabs.find(t=>t.id===active)!;
 const navigate=(id:string)=>{setActive(id);setMenuOpen(false);};
 return <><Head><title>Mercury — The agent economy starts here</title><meta name="description" content="Create, discover, and own AI agents. Mercury connects agent identity, a dedicated wallet, and an on-chain ownership record."/><meta name="theme-color" content="#090b10"/></Head>
 {!token?<Landing login={login} loading={loading} error={error}/>:<div className="workspace"><a className="skip-link" href="#workspace-main">Skip to workspace</a>{menuOpen&&<button className="sidebar-backdrop" aria-label="Close navigation" onClick={()=>setMenuOpen(false)}/>}
 <aside className={`sidebar ${menuOpen?"is-open":""}`}><a href="/" className="brand"><Mark/><span>mercury<span className="brand-dot">.</span></span></a><div className="workspace-label">YOUR WORKSPACE <span>01</span></div><nav aria-label="Workspace navigation">{tabs.map((tab,i)=><button key={tab.id} className={`nav-item ${active===tab.id?"active":""} ${i===6?"nav-settings":""}`} aria-current={active===tab.id?"page":undefined} onClick={()=>navigate(tab.id)}><Icon name={tab.icon}/><span>{tab.name}</span>{active===tab.id&&<i/>}</button>)}</nav><div className="sidebar-note"><span className="mini-orb"/><strong>Intelligence, with ownership.</strong><p>Build an agent.<br/>Start something bigger.</p><button onClick={()=>navigate("studio")}>Create an agent <Icon name="arrow"/></button></div><div className="sidebar-bottom"><span className="status-dot"/> Mercury workspace <span>v0.1</span></div></aside>
 <div className="workspace-body"><header className="workspace-topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={()=>setMenuOpen(true)}><Icon name="menu"/></button><span>Workspace</span><span>/</span><strong>{current.name}</strong></div><div className="account-actions"><span className="session-chip"><span className="status-dot"/>Wallet session</span><button className="wallet-chip" onClick={()=>navigate("settings")}><span className="wallet-avatar"/>{address?`${address.slice(0,6)}…${address.slice(-4)}`:"Connected wallet"}<Icon name="chevron"/></button><button className="icon-button disconnect" onClick={signOut} aria-label="Disconnect wallet" title="Disconnect wallet"><Icon name="logout"/></button></div></header>
 <main id="workspace-main" className="workspace-main"><div className="page-heading"><div><p className="eyebrow">MERCURY / {current.name.toUpperCase()}</p><h1>{current.name}<span className="heading-dot">.</span></h1><p>{current.subtitle}</p></div>{active!=="studio"&&<button className="button primary" onClick={()=>navigate("studio")}><Icon name="plus"/>Create agent</button>}</div><div className="surface" key={active}>{active==="overview"&&<Dashboard onNavigate={navigate}/>} {active==="marketplace"&&<Marketplace/>}{active==="studio"&&<AgentStudio/>}{active==="chat"&&<ChatInterface/>}{active==="activity"&&<SmartContractIntegration/>}{active==="payments"&&<PaymentFlow/>}{active==="settings"&&<Settings/>}</div><footer className="workspace-footer"><span>Mercury · AI agent ownership & marketplace</span><span>Built for the next agent economy <Icon name="spark"/></span></footer></main></div></div>}</>;
}
