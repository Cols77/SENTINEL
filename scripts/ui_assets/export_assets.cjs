/* Re-export deterministic PNG/ICO assets from vector masters. Requires sharp. */
const fs=require('fs'),path=require('path'),sharp=require('sharp');
const root=path.resolve(__dirname,'../../assets/sentinel-ui');
async function raster(svg,file,width){
  fs.mkdirSync(path.dirname(file),{recursive:true});
  await sharp(svg,{density:300}).resize({width}).png().toFile(file);
}
async function main(){
  for(const variant of ['light','dark']){
    for(const size of [32,64,128,256,512])await raster(path.join(root,`brand/symbol-${variant}.svg`),path.join(root,`brand/png/symbol-${variant}-${size}.png`),size);
    for(const size of [512,1024])await raster(path.join(root,`brand/logo-${variant}.svg`),path.join(root,`brand/png/logo-${variant}-${size}.png`),size);
    for(const size of [16,24,32,48,180])await raster(path.join(root,`brand/symbol-compact-${variant}.svg`),path.join(root,`brand/favicon/compact-${variant}-${size}.png`),size);
    const sizes=[16,32,48],pngs=sizes.map(s=>fs.readFileSync(path.join(root,`brand/favicon/compact-${variant}-${s}.png`)));
    const header=Buffer.alloc(6+16*sizes.length);header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);
    let offset=header.length;
    sizes.forEach((s,i)=>{const n=6+i*16;header[n]=s;header[n+1]=s;header.writeUInt16LE(1,n+4);header.writeUInt16LE(32,n+6);header.writeUInt32LE(pngs[i].length,n+8);header.writeUInt32LE(offset,n+12);offset+=pngs[i].length;});
    fs.writeFileSync(path.join(root,`brand/favicon/favicon-${variant}.ico`),Buffer.concat([header,...pngs]));
    fs.copyFileSync(path.join(root,`brand/symbol-compact-${variant}.svg`),path.join(root,`brand/favicon/favicon-${variant}.svg`));
  }
  await raster(path.join(root,'brand/source-aligned-reconstruction.svg'),path.join(root,'brand/png/source-aligned-reconstruction.png'),1024);
  console.log('Exported 25 transparent PNGs, 2 SVG favicons and 2 ICOs.');
}
main().catch(e=>{console.error(e.message);process.exitCode=1});
