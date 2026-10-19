const assert=require('node:assert/strict');
const crypto=require('node:crypto');
module.exports=function pcProofServer(){
  const challenges=new Map();
  return {
    issue(body){
      const raw=Buffer.from(body.public_key,'base64');
      assert.equal(raw.length,32);
      assert.equal(crypto.createHash('sha256').update(raw).digest('hex'),body.fingerprint);
      const id='synthetic-'+crypto.randomBytes(12).toString('hex'),nonce=crypto.randomBytes(24).toString('base64url');
      challenges.set(id,{raw,nonce,fp:body.fingerprint});return {ok:true,challenge_id:id,nonce};
    },
    verify(body,employee=''){
      const proof=body.pc_proof,c=challenges.get(proof?.challenge_id);assert(c,'one-use PC challenge required');
      const message=[proof.challenge_id,c.nonce,employee,body.username.trim().toLowerCase(),crypto.createHash('sha256').update(body.password).digest('hex')].join('\n');
      const key=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),c.raw]),format:'der',type:'spki'});
      assert(crypto.verify(null,Buffer.from(message),key,Buffer.from(proof.signature,'base64')),'PC signature invalid');
      assert.equal(body.device.fp,c.fp);challenges.delete(proof.challenge_id);
    }
  };
};
