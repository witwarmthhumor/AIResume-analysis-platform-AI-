# -*- coding: utf-8 -*-
import zipfile, re
from collections import Counter
z = zipfile.ZipFile(r'E:\AIDevelop\AIProject\AIResume\tasks\resume-template-20260921\AIResume项目经历-简历模板.docx')
xml = z.read('word/document.xml').decode('utf-8')
print('eastAsia fonts:', Counter(re.findall(r'w:eastAsia="([^"]+)"', xml)))
print('ascii fonts:', Counter(re.findall(r'w:ascii="([^"]+)"', xml)))
print('firstLineChars present:', 'w:firstLineChars' in xml)
print('List Bullet style refs:', xml.count('ListBullet') + xml.count('List Bullet'))
