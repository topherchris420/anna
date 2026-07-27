(function(root,factory){"use strict";var api=factory();if(typeof module==="object"&&module.exports)module.exports=api;root.EngineDemoSearch=api;})(typeof globalThis!=="undefined"?globalThis:this,function(){"use strict";
var MULTI_FILTERS=["source","kind","category","language"];
var REFUSAL="The bundled demo sources do not answer this query. Switch to Live Mode to search the full index.";
// Field weights, mirroring the boosts the live backends apply: a title match
// is worth far more than a body mention.
var FIELD_WEIGHTS={title:8,abstract:3,tags:2,meta:1};
// Term-frequency saturation. Repeating a term stops paying after a few
// occurrences, so a long document cannot outrank a precise one on volume.
var TF_SATURATION=1.5;
// Credit for a prefix rather than exact match ("buffer" against "buffers").
// Deliberately partial: an exact match must always win. Length-gated so short
// tokens like "dma" cannot claim unrelated words.
var PREFIX_CREDIT=0.6;
var PREFIX_MIN_LENGTH=4;
var SNIPPET_CHARS=240;
var SNIPPET_LEAD=80;
function tokens(value){return String(value||"").toLowerCase().match(/[a-z0-9]+/g)||[];}
function termWeight(token,term){if(token===term)return 1;if(token.length<PREFIX_MIN_LENGTH||term.length<PREFIX_MIN_LENGTH)return 0;if(token.indexOf(term)===0||term.indexOf(token)===0)return PREFIX_CREDIT;return 0;}
function matchesTerm(token,term){return termWeight(token,term)>0;}
function countMatches(haystack,queryTerms){var values=tokens(haystack);return queryTerms.reduce(function(count,term){return count+values.filter(function(value){return matchesTerm(value,term);}).length;},0);}
// One field's contribution, plus which query terms it covered — coverage is
// scored once across all fields, below.
function fieldScore(haystack,queryTerms){var matched=0;var covered=Object.create(null);tokens(haystack).forEach(function(value){queryTerms.forEach(function(term){var weight=termWeight(value,term);if(weight>0){matched+=weight;covered[term]=true;}});});return {score:matched?matched/(matched+TF_SATURATION):0,covered:covered};}
function scoreDocument(doc,query,queryTerms){if(!queryTerms.length)return 1;
var fields={title:String(doc.title||""),abstract:String(doc.abstract||"")+" "+String(doc.body||""),tags:(doc.tags||[]).concat(doc.categories||[]).join(" "),meta:String(doc.source||"")+" "+String(doc.kind||"")};
var score=0;var covered=Object.create(null);
Object.keys(FIELD_WEIGHTS).forEach(function(name){var result=fieldScore(fields[name],queryTerms);score+=FIELD_WEIGHTS[name]*result.score;Object.keys(result.covered).forEach(function(term){covered[term]=true;});});
if(!score)return 0;
// Coverage dominates: a document carrying every query term outranks one that
// repeats a single term, which is what counting raw matches got wrong.
var distinct=queryTerms.filter(function(term,index){return queryTerms.indexOf(term)===index;});
var coverage=distinct.filter(function(term){return covered[term];}).length/distinct.length;
score*=0.25+0.75*coverage;
// Contiguous-phrase bonus, matching the phrase clause both live backends use.
var lowerQuery=query.toLowerCase();
if(fields.title.toLowerCase().indexOf(lowerQuery)>=0)score*=1.75;
else if(fields.abstract.toLowerCase().indexOf(lowerQuery)>=0)score*=1.35;
return score;}
function truncate(text){return text.length>SNIPPET_CHARS?text.slice(0,SNIPPET_CHARS).replace(/\s+\S*$/,"")+"…":text;}
// A query-focused fragment carrying the same contract as the Elasticsearch and
// Postgres highlighters: raw document text with <em> around matched words,
// escaping left to the consumer. Returning the whole abstract unmarked — as
// this used to — is not a highlight; it tells the reader nothing about why the
// document matched.
function snippet(doc,queryTerms){var text=String(doc.abstract||doc.body||"").replace(/\s+/g," ").trim();if(!text)return [];if(!queryTerms.length)return [truncate(text)];
var word=/[a-z0-9]+/gi;var match;var hit=-1;
while((match=word.exec(text))!==null){var value=match[0].toLowerCase();if(queryTerms.some(function(term){return matchesTerm(value,term);})){hit=match.index;break;}}
if(hit<0)return [];
var start=Math.max(0,hit-SNIPPET_LEAD);
if(start>0){var space=text.indexOf(" ",start);start=space>=0&&space<hit?space+1:start;}
var end=Math.min(text.length,start+SNIPPET_CHARS);
if(end<text.length){var lastSpace=text.lastIndexOf(" ",end);if(lastSpace>start)end=lastSpace;}
var fragment=text.slice(start,end).replace(/[a-z0-9]+/gi,function(value){return queryTerms.some(function(term){return matchesTerm(value.toLowerCase(),term);})?"<em>"+value+"</em>":value;});
return [(start>0?"…":"")+fragment+(end<text.length?"…":"")];}
function includesAny(selected,values){if(!selected||!selected.length)return true;return selected.some(function(item){return values.indexOf(String(item).toLowerCase())>=0;});}
function matchesFilters(doc,filters){filters=filters||{};for(var i=0;i<MULTI_FILTERS.length;i+=1){var key=MULTI_FILTERS[i];var values=key==="category"?doc.categories||[]:[doc[key]==null?"":String(doc[key])];values=values.map(function(value){return String(value).toLowerCase();});if(!includesAny(filters[key],values))return false;}if(filters.has_code==="true"&&!doc.has_code)return false;if(filters.has_equations==="true"&&!doc.has_equations)return false;return true;}
function facet(rows,key){var counts=Object.create(null);rows.forEach(function(row){var values=key==="category"?row.doc.categories||[]:[row.doc[key]];values.forEach(function(value){if(value==null||value==="")return;counts[value]=(counts[value]||0)+1;});});return Object.keys(counts).sort().map(function(value){return {value:value,count:counts[value]};});}
function search(corpus,request){request=request||{};var query=String(request.q||"").trim();var queryTerms=tokens(query);var page=Math.max(1,Number(request.page)||1);var perPage=Math.max(1,Math.min(100,Number(request.per_page)||20));var rows=corpus.filter(function(doc){return matchesFilters(doc,request.filters);}).map(function(doc){return {doc:doc,score:scoreDocument(doc,query,queryTerms)};}).filter(function(row){return !queryTerms.length||row.score>0;}).sort(function(a,b){return b.score-a.score||a.doc.id.localeCompare(b.doc.id);});var start=(page-1)*perPage;return {query:query,mode:"demo-lexical",total:rows.length,page:page,per_page:perPage,took_ms:0,facets:{source:facet(rows,"source"),kind:facet(rows,"kind"),category:facet(rows,"category"),language:facet(rows,"language"),has_code:[{value:true,count:rows.filter(function(row){return row.doc.has_code;}).length}],has_equations:[{value:true,count:rows.filter(function(row){return row.doc.has_equations;}).length}]},hits:rows.slice(start,start+perPage).map(function(row){return {score:Number(row.score.toFixed(6)),highlights:snippet(row.doc,queryTerms),document:row.doc};})};}
function sentences(text){return String(text||"").replace(/\s+/g," ").split(/(?<=[.!?])\s+/).filter(function(sentence){return sentence.length>20;});}
function summarize(corpus,query,documentIds){var queryTerms=tokens(query);var byId=Object.create(null);corpus.forEach(function(doc){byId[doc.id]=doc;});var chosen=[];(documentIds||[]).forEach(function(id){var doc=byId[id];if(!doc)return;sentences(doc.abstract||doc.body).forEach(function(sentence){var overlap=countMatches(sentence,queryTerms);if(overlap>0)chosen.push({doc:doc,sentence:sentence,overlap:overlap});});});chosen.sort(function(a,b){return b.overlap-a.overlap||a.doc.id.localeCompare(b.doc.id);});chosen=chosen.slice(0,4);if(!chosen.length)return {query:query,answer:REFUSAL,generator:"demo-extractive",citations:[]};var citations=[];var numberById=Object.create(null);var answer=chosen.map(function(item){if(!numberById[item.doc.id]){numberById[item.doc.id]=citations.length+1;citations.push({n:citations.length+1,id:item.doc.id,title:item.doc.title,url:item.doc.url||item.doc.pdf_url,source:item.doc.source});}return item.sentence+" ["+numberById[item.doc.id]+"]";}).join(" ");return {query:query,answer:answer,generator:"demo-extractive",citations:citations};}
function createProvider(corpus){return {health:function(){return Promise.resolve({ready:true,provider:"demo",backend:"bundled",retrieval:"demo-lexical",vector_search:false,document_count:corpus.length,label:"Demo · "+corpus.length+" bundled documents"});},search:function(request){return Promise.resolve(search(corpus,request));},summarize:function(request){return Promise.resolve(summarize(corpus,request.query,request.documentIds));},sources:function(){var names=Array.from(new Set(corpus.map(function(doc){return doc.source;}))).sort();return Promise.resolve({sources:names.map(function(name){return {name:name,display_name:name+" (demo)"};})});}};}
return {createProvider:createProvider,search:search,summarize:summarize,snippet:snippet,tokens:tokens};
});
