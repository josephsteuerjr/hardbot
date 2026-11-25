"""Build the editable Word files and XeLaTeX sources from the two handbooks."""
import re
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent
EDITION = "24 ноября 2026"
BOOKS = {"engineering": ("CRM-SNG-Engineering", "Разработка и сопровождение"),
         "user": ("CRM-SNG-User", "Руководство по ролям")}
INK, ACCENT, MUTED = "202A2C", "8C452A", "515E60"


def read_book(name):
    lines = (BASE / (name + ".md")).read_text(encoding="utf-8").splitlines()
    chapters, code = [], None
    for line in lines[2:]:
        if line.startswith("```"):
            if code is None:
                code = []
            else:
                chapters[-1][1].append(("code", "\n".join(code)))
                code = None
        elif code is not None:
            code.append(line)
        elif line.startswith("## "):
            chapters.append((line[3:], []))
        elif line.strip():
            if line.startswith("### "):
                item = ("sub", line[4:])
            elif line.startswith("> "):
                item = ("note", line[2:])
            elif line.startswith("- "):
                item = ("bullet", line[2:])
            elif re.match(r"^\d+\. ", line):
                item = ("step", line)
            elif line.startswith("!["):
                match = re.fullmatch(r"!\[(.+?)\]\((.+?)\)", line)
                item = ("image", match.groups())
            else:
                item = ("p", line)
            chapters[-1][1].append(item)
    assert code is None and chapters
    return lines[0][2:], lines[1], chapters


def field(paragraph, name, fallback):
    element = OxmlElement("w:fldSimple")
    element.set(qn("w:instr"), name)
    run, value = OxmlElement("w:r"), OxmlElement("w:t")
    value.text = str(fallback)
    run.append(value)
    element.append(run)
    paragraph._p.append(element)


def add_text(paragraph, text):
    for position, part in enumerate(text.split("`")):
        run = paragraph.add_run(part)
        if position % 2:
            run.font.name, run.font.size = "Consolas", Pt(9.6)


def word(book, stem, audience):
    title, intro, chapters = book
    doc = Document()
    doc.core_properties.title = title
    doc.core_properties.author = "CRM СНГ"
    doc.core_properties.subject = audience
    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21)
    section.top_margin, section.bottom_margin = Cm(2.45), Cm(2.1)
    section.left_margin, section.right_margin = Cm(2.35), Cm(2.35)
    section.header_distance, section.footer_distance = Cm(1.05), Cm(1)
    def style(name, font, size, color=INK, bold=False, before=0, after=7):
        s = doc.styles[name] if name in doc.styles else doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        s.font.name, s.font.size, s.font.bold = font, Pt(size), bold
        s.font.color.rgb = RGBColor.from_string(color)
        fmt = s.paragraph_format
        fmt.space_before, fmt.space_after, fmt.line_spacing = Pt(before), Pt(after), 1.12
        return s
    normal=style("Normal","Calibri",12,after=7)
    language=OxmlElement("w:lang")
    language.set(qn("w:val"),"ru-RU")
    normal._element.get_or_add_rPr().append(language)
    st=style("Title","Georgia",30,"111111",after=13)
    for border in st._element.xpath("./w:pPr/w:pBdr"):
        border.getparent().remove(border)
    style("Heading 1","Georgia",24,"182020",after=14)
    style("Heading 2","Calibri",14,ACCENT,True,before=8,after=4)
    style("Book metadata","Calibri",10,MUTED,True,after=12)
    style("Book contents","Calibri",11.5,after=4)
    style("Book running","Calibri",9,MUTED,after=0)
    style("Book code","Consolas",9.2,after=6).paragraph_format.line_spacing = 1.03
    style("Book caption","Calibri",9,MUTED,after=7)
    style("Book note","Calibri",11.5,INK,False,7,7)
    style("Book list","Calibri",12,after=5)
    lp = doc.styles["Book list"].paragraph_format
    lp.left_indent, lp.first_line_indent = Cm(.68), Cm(-.68)
    lp.tab_stops.add_tab_stop(Cm(.68))
    def shade(p, fill):
        item=OxmlElement("w:shd")
        item.set(qn("w:fill"), fill)
        p._p.get_or_add_pPr().append(item)
    def running(p, label):
        p.style = "Book running"
        p.paragraph_format.tab_stops.add_tab_stop(Cm(16.3), WD_TAB_ALIGNMENT.RIGHT)
        p.add_run(label)
    running(section.header.paragraphs[0], "CRM СНГ\t"+audience)
    footer=section.footer.paragraphs[0]
    running(footer, EDITION+"\t")
    field(footer,"PAGE",1)
    doc.add_paragraph(title.replace("CRM СНГ","CRM\u00a0СНГ"),"Title")
    doc.add_paragraph("Редакция от "+EDITION+" года","Book metadata")
    add_text(doc.add_paragraph(),intro)
    doc.add_paragraph("Содержание","Heading 2")
    for i,(name,_) in enumerate(chapters,1):
        p=doc.add_paragraph(style="Book contents")
        link=OxmlElement("w:hyperlink")
        link.set(qn("w:anchor"),f"chapter{i}")
        r, t=OxmlElement("w:r"), OxmlElement("w:t")
        t.text = name
        r.append(t); link.append(r); p._p.append(link)
        p.paragraph_format.tab_stops.add_tab_stop(Cm(16.15),WD_TAB_ALIGNMENT.RIGHT,WD_TAB_LEADER.DOTS)
        p.add_run("\t")
        field(p, f"PAGEREF chapter{i} \\h", i+1)
    for i,(name,items) in enumerate(chapters,1):
        h=doc.add_paragraph(style="Heading 1")
        h.paragraph_format.page_break_before=True
        start,end=OxmlElement("w:bookmarkStart"),OxmlElement("w:bookmarkEnd")
        start.set(qn("w:id"),str(i)); start.set(qn("w:name"),f"chapter{i}"); end.set(qn("w:id"),str(i))
        h._p.append(start); h.add_run(name); h._p.append(end)
        for kind,value in items:
            if kind=="sub":
                doc.add_paragraph(value,"Heading 2")
            elif kind=="image":
                caption,path=value
                p=doc.add_paragraph()
                p.paragraph_format.space_after=Pt(2)
                p.paragraph_format.keep_with_next=True
                p.add_run().add_picture(str(BASE/path),width=Cm(14.8))
                doc.add_paragraph(caption,"Book caption")
            elif kind=="code":
                p=doc.add_paragraph(value,"Book code")
                shade(p,"F0F3F2")
            elif kind in ("bullet","step"):
                p=doc.add_paragraph(style="Book list")
                number, body = value.split(". ",1) if kind=="step" else ("•",value)
                p.add_run((number+"." if kind=="step" else number)+"\t").bold=True
                add_text(p,body)
            else:
                p=doc.add_paragraph(style="Book note" if kind=="note" else "Normal")
                if kind=="note":
                    p.paragraph_format.left_indent=Cm(.35)
                    shade(p,"F5EEE9")
                add_text(p,value)
    outfile=BASE/(stem+".docx")
    temporary=outfile.with_suffix(".tmp.docx")
    doc.save(temporary)
    temporary.replace(outfile)


ESCAPE = {"\\":r"\textbackslash{}", "&":r"\&", "%":r"\%", "$":r"\$", "#":r"\#",
          "_":r"\_", "{":r"\{", "}":r"\}", "~":r"\textasciitilde{}", "^":r"\textasciicircum{}",
          "→":r"{\sffamily →}"}


def texchars(s):
    return "".join(ESCAPE.get(c,c) for c in s)


def textext(s):
    parts=[]
    for i,part in enumerate(s.split("`")):
        if i%2:
            # Paths may break cleanly at their own separators.
            value="".join(texchars(c)+(r"\allowbreak{}" if c in "/_-." else "") for c in part)
            parts.append(r"{\small\ttfamily "+value+"}")
        else:
            parts.append(texchars(part))
    return "".join(parts)


def latex(book, stem, audience):
    title,intro,chapters=book
    tex=[r"""% !TEX program = xelatex
\documentclass[12pt,a4paper]{article}
\usepackage{fontspec,geometry,xcolor,graphicx,enumitem,fancyhdr,hyperref}
\geometry{left=24mm,right=24mm,top=24mm,bottom=22mm,headheight=15pt}
\IfFontExistsTF{Georgia}{\setmainfont{Georgia}}{\setmainfont{DejaVu Serif}}
\IfFontExistsTF{Calibri}{\setsansfont{Calibri}}{\setsansfont{DejaVu Sans}}
\IfFontExistsTF{Consolas}{\setmonofont{Consolas}}{\setmonofont{DejaVu Sans Mono}}
\definecolor{ink}{HTML}{202A2C}
\definecolor{accent}{HTML}{8C452A}
\definecolor{muted}{HTML}{515E60}
\definecolor{rulecolor}{HTML}{D9DEDB}
\color{ink}\setlength{\parindent}{0pt}\setlength{\parskip}{5.5pt}
\setlength{\emergencystretch}{4em}\raggedright
\pagestyle{fancy}\fancyhf{}
\renewcommand{\headrulewidth}{0.25pt}
\fancyhead[L]{\small\sffamily\color{muted}CRM СНГ}
\fancyfoot[R]{\small\sffamily\color{muted}\thepage}
\setlist{leftmargin=1.7em,topsep=4pt,itemsep=4pt,parsep=0pt}
\newcommand{\booktitle}[1]{{\fontsize{29}{34}\selectfont #1\par}}
\newcommand{\chapterhead}[1]{{\fontsize{23}{27}\selectfont #1\par}\vspace{5pt}}
\newcommand{\subhead}[1]{\par\vspace{4pt}{\large\sffamily\bfseries\color{accent}#1\par}\nopagebreak}
\hypersetup{hidelinks,unicode=true,bookmarksnumbered=false,pdfauthor={CRM СНГ},pdflang={ru-RU}}
""",rf"\hypersetup{{pdftitle={{{texchars(title)}}}}}",
         rf"\fancyhead[R]{{\small\sffamily\color{{muted}}{texchars(audience)}}}",
         rf"\fancyfoot[L]{{\small\sffamily\color{{muted}}{EDITION}}}",
         r"\begin{document}",r"\pdfbookmark[0]{Содержание}{contents}",r"\booktitle{"+texchars(title).replace("CRM СНГ","CRM~СНГ")+"}",
         r"{\small\sffamily\color{muted}Редакция от "+EDITION+r" года\par}",textext(intro),
         r"\subhead{Содержание}"]
    for i,(name,_) in enumerate(chapters,1):
        tex.append(rf"{{\sffamily\hyperref[ch{i}]{{{texchars(name)}\dotfill\pageref*{{ch{i}}}}}\par}}")
    for i,(name,items) in enumerate(chapters,1):
        tex += [r"\clearpage",rf"\pdfbookmark[1]{{{texchars(name)}}}{{chapter{i}}}",
                rf"\phantomsection\label{{ch{i}}}\chapterhead{{{texchars(name)}}}"]
        mode=None
        for kind,value in items:
            target={"bullet":"itemize","step":"enumerate"}.get(kind)
            if mode!=target:
                if mode: tex.append(r"\end{"+mode+"}")
                if target: tex.append(r"\begin{"+target+"}")
                mode=target
            if kind=="sub": tex.append(r"\subhead{"+texchars(value)+"}")
            elif kind=="code":
                tex.extend([r"{\footnotesize\begin{verbatim}",value,r"\end{verbatim}}"])
            elif kind=="image":
                caption,path=value
                tex.append(r"\begin{minipage}{\linewidth}\vspace{2pt}\includegraphics[width=.91\linewidth]{"+path+r"}\par{\small\sffamily\color{muted}"+texchars(caption)+r"\par}\end{minipage}\par")
            elif kind=="note":
                tex.append(r"\begin{quote}\sffamily\color{accent}"+textext(value)+r"\end{quote}")
            elif target:
                tex.append(r"\item "+textext(value.split(". ",1)[1] if kind=="step" else value))
            else: tex.append(textext(value)+r"\par")
        if mode: tex.append(r"\end{"+mode+"}")
    tex.append(r"\end{document}")
    target=BASE/(stem+".tex")
    temporary=target.with_suffix(".tmp.tex")
    temporary.write_text("\n".join(tex)+"\n",encoding="utf-8")
    temporary.replace(target)


if __name__=="__main__":
    for key,(filename,audience) in BOOKS.items():
        book=read_book(key)
        word(book,filename,audience)
        latex(book,filename,audience)
