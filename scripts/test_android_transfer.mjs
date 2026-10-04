import { connect } from './android_cdp.mjs'
import { writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
const page = await connect()
const bytes = await page.evaluate(`window.Capacitor.nativePromise('Reader','exportFile',{path:'/api/data/book-transfer',method:'GET',body:''}).then(f=>window.Capacitor.nativePromise('Filesystem','readFile',{path:f.uri})).then(f=>f.data)`)
const request = (method,path,body) => page.evaluate(`fetch(${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json'},body:${body === undefined ? 'undefined' : `JSON.stringify(${JSON.stringify(body)})`}}).then(async r=>({status:r.status,body:await r.json()}))`)
const original = (await request('GET','/api/me')).body
try {
  const account = await request('POST','/api/auth/local/register',{display_name:'安卓迁移验收',username:`android-test-${Date.now()}`})
  assert.equal(account.status,200)
  assert.notEqual(account.body.user_id,original.user_id)
  assert.equal((await request('GET','/api/library/index')).body.books.length,0)
  const imported = await page.evaluate(`(async()=>{const bytes=Uint8Array.from(atob(${JSON.stringify(bytes)}),c=>c.charCodeAt(0));const form=new FormData();form.append('file',new File([bytes],'迁移验收.zip',{type:'application/zip'}));const r=await fetch('/api/data/book-transfer/import',{method:'POST',body:form});return {status:r.status,body:await r.json()}})()`)
  assert.equal(imported.status,200,JSON.stringify(imported))
  const index=(await request('GET','/api/library/index')).body
  const chapter=index.chapters.find(c=>c.bookId==='book_09391a9e-49fc-4fa8-8e70-125e14390c80')
  assert.ok(chapter)
  const detail=(await request('GET',`/api/library/chapters/${chapter.id}`)).body
  assert.equal(detail.sentences.length,5)
  const tokenDetail=(await request('GET',`/api/library/chapters/${chapter.id}/details`)).body
  assert.ok(tokenDetail.tokens.length>20)
  const cards=(await request('GET','/api/cards?q=猫')).body
  if (!JSON.stringify(cards).includes('猫')) console.log({imported:imported.body, cards, allCards:(await request('GET','/api/cards')).body, summary:(await request('GET','/api/cards/summary')).body})
  assert.ok(JSON.stringify(cards).includes('猫'))
  await writeFile('build/android-transfer-test.json',JSON.stringify({newAccountIsolated:true,books:index.books.length,sentences:detail.sentences.length,tokens:tokenDetail.tokens.length,cardsPreserved:true},null,2))
  console.log('Native transfer/import and account isolation passed')
} finally {
  assert.equal((await request('POST','/api/auth/local/switch',{user_id:original.user_id})).status,200)
  page.close()
}
