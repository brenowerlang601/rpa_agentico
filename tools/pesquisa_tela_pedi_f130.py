import asyncio
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError


async def pesquisa_tela_pedi_f130(page: Page):
    # Ação 1: evaluate JS (primeira tentativa de busca)
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){try{var inp = document.querySelector('input[placeholder=\"Buscar programas\"], input[aria-placeholder=\"Buscar programas\"], input[placeholder*=\"Buscar programas\"], input[aria-label*=\"Buscar programas\"]');\nif(!inp){return 'input not found';}\ninp.focus();\nvar value = 'pedi_f130';\nvar last = inp.value;\ninp.value = value;\nvar ev1 = new Event('input', {bubbles:true});\nvar ev2 = new Event('change', {bubbles:true});\ninp.dispatchEvent(ev1);\ninp.dispatchEvent(ev2);\nvar eDown = new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true});\nvar eUp = new KeyboardEvent('keyup',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true});\ninp.dispatchEvent(eDown);\ninp.dispatchEvent(eUp);\nreturn 'ok';}catch(e){return 'Error: '+e.message;}})()"
        )
    except PlaywrightTimeoutError:
        pass
    except Exception:
        try:
            locator = page.get_by_placeholder("Buscar programas")
            await locator.fill("pedi_f130")
        except Exception:
            pass

    # Ação 2: wait 3s
    await asyncio.sleep(0.5)
    await asyncio.sleep(3)

    # Ação 3: evaluate JS (segunda tentativa de busca)
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){try{var input=document.querySelector('input[placeholder=\"Buscar programas\"]')||document.querySelector('input[aria-label*=\"Buscar programas\"]')||document.querySelector('input[title*=\"Buscar programas\"]');if(!input){return 'input not found';}input.focus();input.value='pedi_f130';var evInput=new Event('input',{bubbles:true});input.dispatchEvent(evInput);var evChange=new Event('change',{bubbles:true});input.dispatchEvent(evChange);var evDown=new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true});input.dispatchEvent(evDown);var evUp=new KeyboardEvent('keyup',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true});input.dispatchEvent(evUp);return 'ok';}catch(e){return 'Error: '+e.message;}})()"
        )
    except Exception:
        try:
            locator = page.get_by_role("textbox", name="Buscar programas")
            await locator.fill("pedi_f130")
        except Exception:
            pass

    # Ação 4: evaluate JS (terceira tentativa de busca)
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){\n  try {\n    var input = document.querySelector(\"input[placeholder='Buscar programas']\") ||\n                document.querySelector(\"input[aria-label='Buscar programas']\") ||\n                document.querySelector(\"input[title='Buscar programas']\");\n\n    if (!input) {\n      return 'search input not found';\n    }\n\n    input.focus();\n    input.value = 'pedi_f130';\n\n    var inputEvent = new Event('input', { bubbles: true });\n    input.dispatchEvent(inputEvent);\n    var changeEvent = new Event('change', { bubbles: true });\n    input.dispatchEvent(changeEvent);\n\n    function keyEvent(type) {\n      var e = new KeyboardEvent(type, {\n        bubbles: true,\n        cancelable: true,\n        key: 'Enter',\n        code: 'Enter',\n        keyCode: 13,\n        which: 13\n      });\n      input.dispatchEvent(e);\n    }\n    keyEvent('keydown');\n    keyEvent('keypress');\n    keyEvent('keyup');\n\n    return 'search triggered';\n  } catch (e) {\n    return 'Error: ' + e.message;\n  }\n})();"
        )
    except Exception:
        try:
            locator = page.get_by_placeholder("Buscar programas")
            await locator.fill("pedi_f130")
        except Exception:
            pass

    # Ação 5: evaluate JS (quarta tentativa, busca recursiva)
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){try{function findBuscarInput(root){const elements=root.querySelectorAll('input,textarea,[contenteditable=\"true\"]');for(const el of elements){const ph=(el.getAttribute('placeholder')||'').toLowerCase();const aria=(el.getAttribute('aria-label')||'').toLowerCase();if(ph.includes('buscar programas')||aria.includes('buscar programas')){return el;}}const all=root.querySelectorAll('*');for(const el of all){if(el.shadowRoot){const found=findBuscarInput(el.shadowRoot);if(found)return found;}}return null;}var input=findBuscarInput(document);if(!input){return 'search input not found';}input.focus();input.value='';var inputEvent=new Event('input',{bubbles:true});input.dispatchEvent(inputEvent);input.value='pedi_f130';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));var ke=new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',code:'Enter',keyCode:13,which:13});input.dispatchEvent(ke);var kup=new KeyboardEvent('keyup',{bubbles:true,cancelable:true,key:'Enter',code:'Enter',keyCode:13,which:13});input.dispatchEvent(kup);var kpress=new KeyboardEvent('keypress',{bubbles:true,cancelable:true,key:'Enter',code:'Enter',keyCode:13,which:13});input.dispatchEvent(kpress);return 'search input filled and enter dispatched';}catch(e){return 'error: '+e.message;}})()"
        )
    except Exception:
        try:
            locator = page.get_by_placeholder("Buscar programas")
            await locator.clear()
            await locator.fill("pedi_f130")
        except Exception:
            pass

    # Ação 6: evaluate JS (quinta tentativa, treeWalker)
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){\n  try {\n    function findSearchEl(root) {\n      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);\n      while (walker.nextNode()) {\n        const el = walker.currentNode;\n        if (el && el.getAttribute) {\n          const ph = el.getAttribute('placeholder');\n          const aria = el.getAttribute('aria-label');\n          const title = el.getAttribute('title');\n          const text = (ph || aria || title || '').toLowerCase();\n          if (text.includes('buscar programas')) {\n            return el;\n          }\n        }\n        if (el && el.shadowRoot) {\n          const foundInShadow = findSearchEl(el.shadowRoot);\n          if (foundInShadow) return foundInShadow;\n        }\n      }\n      return null;\n    }\n\n    var target = findSearchEl(document);\n    if (!target) {\n      var inputs = Array.from(document.querySelectorAll('input,textarea,[contenteditable=\"true\"]'));\n      target = inputs.find(function(el){\n        const ph = el.getAttribute('placeholder') || '';\n        return ph.toLowerCase().includes('buscar programas');\n      });\n    }\n\n    if (!target) {\n      return 'search element not found';\n    }\n\n    if (typeof target.focus === 'function') target.focus();\n\n    if ('value' in target) {\n      target.value = 'pedi_f130';\n    } else if (target.isContentEditable) {\n      target.textContent = 'pedi_f130';\n    }\n\n    target.dispatchEvent(new Event('input', {bubbles: true}));\n    target.dispatchEvent(new Event('change', {bubbles: true}));\n\n    var evtOptions = {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true};\n    target.dispatchEvent(new KeyboardEvent('keydown', evtOptions));\n    target.dispatchEvent(new KeyboardEvent('keypress', evtOptions));\n    target.dispatchEvent(new KeyboardEvent('keyup', evtOptions));\n\n    return 'search triggered';\n  } catch (e) {\n    return 'error: ' + e.message;\n  }\n})();"
        )
    except Exception:
        try:
            locator = page.get_by_role("textbox", name="Buscar programas")
            await locator.fill("pedi_f130")
        except Exception:
            pass

    # Ação 7: evaluate JS (clicar em texto 'Buscar programas')
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){try{const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT,null,false);let target=null;while(walker.nextNode()){const el=walker.currentNode;try{const txt=(el.textContent||'').trim();if(txt && txt.includes('Buscar programas')){target=el;break;}}catch(e){}}if(!target){return 'search text element not found';}let clickable=target;while(clickable && typeof clickable.click!=='function'){clickable=clickable.parentElement;}if(clickable){clickable.click();return 'clicked search text element';}return 'clickable ancestor not found';}catch(e){return 'Error: '+e.message;}})()"
        )
    except Exception:
        try:
            locator = page.get_by_text("Buscar programas", exact=False)
            await locator.click()
        except Exception:
            pass

    # Ação 8: send_keys "pedi_f130"
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.type("pedi_f130")
    except Exception:
        pass

    # Ação 9: send_keys "Enter"
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass

    # Ação 10: evaluate JS (click relativo na tela)
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            "(function(){try{var w=window.innerWidth||document.documentElement.clientWidth;var h=window.innerHeight||document.documentElement.clientHeight;var x=Math.floor(w*0.8);var y=Math.floor(h*0.08);function clickAt(x,y){var el=document.elementFromPoint(x,y);if(!el){return 'no element at point';}['mousedown','mouseup','click'].forEach(function(type){var ev=new MouseEvent(type,{bubbles:true,cancelable:true,view:window,clientX:x,clientY:y});el.dispatchEvent(ev);});return 'clicked '+(el.tagName||'unknown')+' at '+x+','+y;}return clickAt(x,y);}catch(e){return 'Error: '+e.message;}})()"
        )
    except Exception:
        pass

    # Ação 11: send_keys "pedi_f130" novamente
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.type("pedi_f130")
    except Exception:
        pass

    # Ação 12: send_keys "Enter" novamente
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass

    await asyncio.sleep(0.5)
