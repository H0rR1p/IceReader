const fs = require('node:fs')
const path = require('node:path')
const ResEdit = require('resedit')

module.exports = async function embedWindowsIcon(context) {
  if (context.electronPlatformName !== 'win32') return
  const executable = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.exe`)
  const iconPath = path.join(context.packager.projectDir, 'assets', 'bingdu.ico')
  const binary = ResEdit.NtExecutable.from(fs.readFileSync(executable), { ignoreCert: true })
  const resources = ResEdit.NtExecutableResource.from(binary)
  // Replace every inherited Electron icon, including other language variants.
  resources.entries = resources.entries.filter(entry => entry.type !== 3 && entry.type !== 14)
  const icon = ResEdit.Data.IconFile.from(fs.readFileSync(iconPath))
  ResEdit.Resource.IconGroupEntry.replaceIconsForResource(resources.entries, 101, 1033,
    icon.icons.map(item => item.data))
  for (const version of ResEdit.Resource.VersionInfo.fromEntries(resources.entries)) {
    for (const language of version.getAllLanguagesForStringValues()) {
      version.setStringValues(language, {
        FileDescription: context.packager.appInfo.productName,
        ProductName: context.packager.appInfo.productName,
        InternalName: context.packager.appInfo.productFilename,
        OriginalFilename: `${context.packager.appInfo.productFilename}.exe`,
      })
    }
    version.setFileVersion(context.packager.appInfo.version)
    version.setProductVersion(context.packager.appInfo.version)
    version.outputToResourceEntries(resources.entries)
  }
  resources.outputResource(binary)
  const temporary = executable + '.icon.tmp'
  fs.writeFileSync(temporary, Buffer.from(binary.generate()))
  fs.renameSync(temporary, executable)
  console.log('Embedded bingdu logo in Windows executable')
}
