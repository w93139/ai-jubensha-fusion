import { copyFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const modelsDirectory = join(scriptDirectory, '..', 'src', 'client', 'models');
const generatedSource = join(modelsDirectory, 'APIResponse_List_ScriptCharacter__.ts');
const importedName = join(modelsDirectory, 'APIResponse_list_ScriptCharacter__.ts');

// macOS 默认文件系统不区分大小写，Linux 容器区分。上游生成器产生了
// 大写文件名和小写 import 的组合；仅在需要时创建构建兼容副本。
if (!existsSync(importedName) && existsSync(generatedSource)) {
  copyFileSync(generatedSource, importedName);
}
