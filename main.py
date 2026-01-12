import html
import json
import os

from pathlib import Path
import re
import shutil
from dotenv import load_dotenv
from zipfile import ZipFile
import xml.etree.ElementTree as ET

class Config: 
  def __init__(self):
    load_dotenv()
    self.niagara_home = Path(os.getenv("NIAG_HOME") or "C:/Honeywell/OptimizerSupervisor-N4.15.1.16")
    self.tmp_docs = Path("./tmp")
    self.output = Path(os.getenv("OUTPUT_PATH") or "./data/").resolve()

    self.tmp_docs.mkdir(parents=False, exist_ok=True)
    self.output.parent.mkdir(parents=True, exist_ok=True)

  def cleanup(self):
    if config.tmp_docs.exists() and config.tmp_docs.is_dir():
      if config.tmp_docs.name == "tmp":
          shutil.rmtree(config.tmp_docs)

class Extractor:
  def __init__(self, config: Config):
    self.config = config

  def run(self):
    modules_dir = self.config.niagara_home / "modules"
    if not modules_dir:
      print(f"don't get modules directory ({modules_dir})")
      return

    docs_jar = list(modules_dir.rglob("doc*-doc.jar"))
    if not docs_jar:
      print(f"don't get jar file")
      return

    print(f"extract {len(docs_jar)} modules")

    count = 0
    for jar_path in docs_jar:
      try:
        self._extract_jar(jar_path)
        count += 1
        print(f".", end="", flush=True)
      except Exception as e:
        print(f"can't extract jar ({jar_path.name}) : {e}")

    print(f"\nextract ok in {self.config.tmp_docs}")

  def _extract_jar(self, jar_path: Path):
    with ZipFile(jar_path) as zip:
      for doc in zip.namelist():
        if doc.endswith(".bajadoc"): 
          zip.extract(doc, self.config.tmp_docs)

class Parser:
  def __init__(self, config: Config):
    self.config = config

  def replace_link(self, match):
    target = match.group(1) 
    label = match.group(2) 
    label = " ".join(label.split())
    if not label.strip():
        label = target
      
    return f'<span class="doc-link" data-ref="{target}">{label}</span>'

  def _clean_text(self, text):
    if not text: return ""
    
    text = html.unescape(text)

    text = re.sub(r'&#[xX]?0+;', '', text)

    pattern = r'<see\s+ref=["\'](.*?)["\']\s*>(.*?)<\/see>'
    text = re.sub(pattern, self.replace_link, text, flags=re.DOTALL | re.IGNORECASE)

    return text.strip()

  def _get_full_content(self, element, tag_name):
    child = element.find(tag_name)
    if child is None: return ""
    raw_xml = ET.tostring(child, encoding='unicode')
  
    match = re.search(r'^<[^>]+>(.*)</[^>]+>$', raw_xml, re.DOTALL)
    if match:
        return match.group(1)
    
    return raw_xml
  
  def _safe_parse_xml(self, doc_path):
    try:
      content = Path(doc_path).read_text(encoding="utf-8", errors="replace")
      content = re.sub(r'&#[xX]?0+;', '', content)
      return ET.fromstring(content)
    except ET.ParseError as e:
      return None
    except Exception as e:
      print(f"reader error ({doc_path}) : {e}")
      return None
    
  def _extract_tags(self, element):
    return [{"name": tag.get("name"), "text": tag.text} for tag in element.findall("tag")]
  
  def _extract_params(self, element):
    params = []
    for param in element.findall("parameter"):
      type_node = param.find("type")
      param_type = type_node.get("class") if type_node is not None else "Unknown"
      params.append({
        "name": param.get("name"),
        "type": param_type
      })
    return params

  def _parse_class_detail(self, doc_path):
    empty_data = {"description": "", "extends": [], "implements": [], "tags": [], "properties": [], "actions": []}
    root = self._safe_parse_xml(doc_path)
    
    if root is None: return empty_data
    if root.find("package") is not None: return empty_data

    cls = root.find("class")
    if cls is None: return empty_data

    description_raw = self._get_full_content(cls, "description")

    data = {
      "description": self._clean_text(description_raw),
      "extends": [t.attrib.get("class") for t in cls.findall("extends/type") or []],
      "implements": [t.attrib.get("class") for t in cls.findall("implements/type") or []],
      "class_tags": self._extract_tags(cls),
      "properties": [],
      "actions": []
    }

    for prop in cls.findall("property"):
      type_node = prop.find("type")
      prop_desc_raw = self._get_full_content(prop, "description")
      data["properties"].append({
        "name": prop.get("name"),
        "type": type_node.get("class") if type_node is not None else "Unknown",
        "flags": prop.get("flags", ""),
        "description": self._clean_text(prop_desc_raw),
        "tags": self._extract_tags(prop)
    })
      
    for act in cls.findall("action"):
      return_node = act.find("return/type")
      act_desc_raw = self._get_full_content(act, "description")
      data["actions"].append({
        "name": act.get("name"),
        "returnType": return_node.get("class") if return_node is not None else "void",
        "flags": act.get("flags"),
        "description": self._clean_text(act_desc_raw),
        "parameters": self._extract_params(act),
        "tags": self._extract_tags(act)
    })
      
    return data
  
  def run(self):
    print("\nstart xml analyse")
    full_doc = []

    if not self.config.tmp_docs.exists():
      print("tmp dir not found")
      return 
    
    modules = sorted([d for d in (self.config.tmp_docs / "doc").iterdir() if d.is_dir()])
    output_dir = self.config.output.parent
    modules_out_dir = output_dir / "modules"   
    modules_out_dir.mkdir(parents=True, exist_ok=True)

    search_index = []
    
    for module_dir in modules:
      module_index = module_dir / "module-index.bajadoc"
      if not module_index.exists(): continue

      try:
        module_root = ET.parse(module_index).getroot().find("module")
        mod_alias = f"{module_root.get("name")}-{module_root.get("runtimeProfile")}"
        print(f"work on {mod_alias}")

        module_data = {
          "name": module_root.get("name"),
          "profile": module_root.get("runtimeProfile"),
          "packages": [],
          "alias": mod_alias
        }

        for pkg_file in module_dir.rglob("package-index.bajadoc"):
          try:
            pkg_root = ET.parse(pkg_file).getroot().find("package")
            pkg_name = pkg_root.get("name")

            package_data = {"name": pkg_name, "classes": []}

            for cls_elem in pkg_root.findall("class"):
              cls_name = cls_elem.get("name")
              summary_raw = self._get_full_content(cls_elem, "description")
              summary = self._clean_text(summary_raw)

              search_index.append({
                  "name": cls_name,
                  "module": mod_alias,
                  "package": pkg_name,
                  "summary": (summary[:100] + "...") if summary else ""
              })

              cls_doc_path = next(pkg_file.parent.glob(f"{cls_name}*.bajadoc"), None)
              if not cls_doc_path:
                cls_doc_path = next(pkg_file.parent.glob(f"{cls_name}*.bajadoc"), None)

              details = self._parse_class_detail(cls_doc_path) if cls_doc_path else {}
              if not details.get("description"): details["description"] = summary

              package_data["classes"].append({
                "name": cls_name,
                "summary": summary,
                "details": details
              })
            module_data["packages"].append(package_data)
          except Exception as e:
            print(f"package error {pkg_file} : {e}")

        mod_file = modules_out_dir / f"{mod_alias}.json"
        with open(mod_file, "w", encoding="utf-8") as f:
            json.dump(module_data, f, ensure_ascii=False)
      except Exception as e:
        print(f"ignored module {module_dir.name} : {e}")
    
    index_file = output_dir / "search_index.json"
    print(f"save json in {self.config.output}... ({len(search_index)} entrées)")
    with open(index_file, "w", encoding="utf-8") as f:
      json.dump(search_index, f, ensure_ascii=False)
    print("parsing ok")



if __name__ == "__main__":
  config = Config()
  extractor = Extractor(config)
  extractor.run()
  parser = Parser(config)
  parser.run()

  config.cleanup()


    
