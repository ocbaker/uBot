import aiohttp
import bs4
import feedparser
import re
import tomd

import discord
from discord.ext import commands

headerEx = re.compile(r"((^<br/>$)|(This (article|page)))")
referEx = re.compile(r".*? may refer to\:")
linkEx = re.compile(r"\((\/\S*)\)")
fontEx = re.compile(r"<h\d>(.*?)(<font.*>(.*?)</font>)?</h\d>")
langEx = re.compile(r"/(cs|de|es|fr|it|ja|nl|pl|pt-br|ru|sv|uk|zh|tr|ko|ms|da|hu|vi|pt-pt)$")
fffEx = re.compile(r"Friday Facts #(\d*)")
markdownEx = re.compile(r"([~*_`])")


async def get_soup(url):
    """
    Returns a list with the response code (as int) and a BeautifulSoup object of the URL
    """
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as resp:
            status = resp.status
            r = await resp.text()
    return (status, bs4.BeautifulSoup(r, "html.parser"))


def mod_embed(result):
    """
    Returns a discord.Embed object derived from a mod page BeautifulSoup
    """
    taglist = []
    fields = []
    title = result.find("div", class_="mod-card-info-container").find("h2", class_="mod-card-title").find("a")
    summary = result.find("div", class_="mod-card-info-container").find("div", class_="mod-card-summary").string
    em = discord.Embed(title=title.string,
                       url=f"https://mods.factorio.com{title['href'].replace(' ', '%20')}",
                       description=markdownEx.sub(r"\\\1", summary),
                       colour=discord.Colour.dark_green())
    thumbnail = result.find("div", class_="mod-card-thumbnail")
    if "no-picture" not in thumbnail.attrs["class"]:
        em.set_thumbnail(url=thumbnail.find("a").find("img")["src"])
    owner = result.find("div", class_="mod-card-info-container").find("div", class_="mod-card-author").find("a")
    fields.append({"name": "Owner", "value": f"[{owner.string}](https://mods.factorio.com{owner['href']})"})
    for tag in result.find("div", class_="mod-card-footer").find("ul").find_all("li", class_="tag"):
        tag = tag.find("span").find("a")
        taglist.append(f"[{tag.string}](https://mods.factorio.com{tag['href']})")
    gameVersions = result.find("div", class_="mod-card-info").find("span", title="Available for these Factorio versions")
    downloads = result.find("div", class_="mod-card-info").find("span", title="Downloads")
    createdAt = result.find("div", class_="mod-card-info").find("span", title="Last updated")
    fields.extend([{"name": "Category", "value": "None" if len(taglist) == 0 else ", ".join(taglist)},
                   {"name": "Game Version(s)", "value": gameVersions.find("div", class_="mod-card-info-tag-label").string},
                   {"name": "Downloads", "value": downloads.find("div", class_="mod-card-info-tag-label").string},
                   {"name": "Updated", "value": createdAt.find("div", class_="mod-card-info-tag-label").string}])
    for field in fields:
        em.add_field(**field, inline=True)
    return em


def get_wiki_description(soup):
    """
    Returns the first paragraph of a wiki page BeautifulSoup
    """
    if soup.select(".mw-parser-output > p"):
        pNum = 0
        if headerEx.search(str(soup.select(".mw-body-content > #mw-content-text > .mw-parser-output > p")[0])):
            pNum = 1
        return tomd.convert(str(soup.select(".mw-body-content > #mw-content-text > .mw-parser-output > p")[pNum])).strip().replace("<br/>", "\n")
    return ""


async def embed_fff(number):
    """
    Returns a discord.Embed object derived from an fff number
    """
    link = f"https://factorio.com/blog/post/fff-{number}"
    response = await get_soup(link)
    if response[0] == 200:
        soup = response[1]
        titleList = soup.find_all("h2")
        em = discord.Embed(title=titleList[0].string.strip(),
                           url=link,
                           colour=discord.Colour.dark_green())
        titleList = titleList[1:]
        if len(titleList) == 0:
            titleList = soup.find_all("h4")
        if len(titleList) == 0:
            titleList = soup.find_all("h3")
        for title in titleList:
            # Check for smaller font tag and append it to the title
            result = fontEx.search(str(title))
            if len([group for group in result.groups() if group is not None]) == 1:
                name = result.group(1)
            else:
                name = result.group(1) + result.group(3)
            content = str(title.next_sibling.next_sibling)
            if "<p>" not in content:
                continue
            if "<ol>" in content:
                itemCount = 1
                while "<li>" in content:
                    content = content.replace("<li>", f"{itemCount}. ", 1)
                    itemCount += 1
            if "<ul>" in content:
                content = content.replace("<li>", "- ")
            for item in ["<ol>", "</ol>", "<ul>", "</ul>", "</li>", "<br/>"]:
                content = content.replace(item, "")
            # Escape Discord formatting characters
            for item in ["*", "_"]:
                content = content.replace(item, "\\" + item)
            content = content.replace("\n\n", "\n")
            em.add_field(name=name.replace("amp;", ""),
                         value=tomd.convert(content).strip())
    else:
        em = discord.Embed(title="Error",
                           description=f"Couldn't find FFF #{number}.",
                           colour=discord.Colour.red())
    return em


async def wiki_embed(url):
    soup = (await get_soup(url))[1]
    description = get_wiki_description(soup)
    baseURL = "wiki.factorio.com" if not url.startswith('stable.') else "stable.wiki.factorio.com"
    templateURL = r"(https://stable.wiki.factorio.com\1)" if url.startswith('stable.') else r"(https://wiki.factorio.com\1)"
    if "may refer to:" in description:
        url = soup.select(".mw-parser-output > ul > li > a")[0]["href"]
        description = get_wiki_description((await get_soup(url))[1])

    em = discord.Embed(title=soup.find("h1", id="firstHeading").get_text(),
                       description=linkEx.sub(templateURL, description),
                       url=url,
                       colour=discord.Colour.dark_green())
    if soup.find("div", class_="factorio-icon"):
        em.set_thumbnail(url=f"https://{baseURL}{soup.find('div', class_='factorio-icon').find('img')['src']}")
    return em


async def process_wiki(ctx, searchterm, stable=False):
    if not searchterm:
        em = discord.Embed(title="Error",
                           description="To use this command, you have to enter a term to search for.",
                           colour=discord.Colour.red())
    baseURL = "wiki.factorio.com" if not stable else "stable.wiki.factorio.com"
    em = discord.Embed(title=f"Searching for \"{searchterm.title()}\" in {baseURL}...",
                       description="This shouldn't take long.",
                       colour=discord.Colour.gold())
    bufferMsg = await ctx.send(embed=em)
    async with ctx.channel.typing():
        url = f"https://{baseURL}/index.php?search={searchterm.replace(' ', '%20')}"
        soup = (await get_soup(url))[1]
        if soup.find("p", class_="mw-search-nonefound"):
            em = discord.Embed(title="Error",
                               description=f"Could not find \"{searchterm.title()}\" in {'' if not stable else 'stable '}wiki.",
                               colour=discord.Colour.red())
            await bufferMsg.edit(embed=em) if ctx.prefix is not None else await bufferMsg.delete()
        else:
            if soup.find_all("ul", class_="mw-search-results"):
                engResults = []
                em = discord.Embed(title=f"Factorio {'' if not stable else 'Stable '}Wiki",
                                   url=url,
                                   colour=discord.Colour.gold())
                for item in soup.find_all("ul", class_="mw-search-results")[0].find_all("li"):
                    item = item.find_next("div", class_="mw-search-result-heading").find("a")
                    if langEx.search(item["title"]) is None:
                        engResults.append(item)
                if (len(engResults) > 0):
                    itemLink = item["href"] if not item["href"].endswith(")") else item["href"].replace(")", "\\)")
                    em.add_field(name=item["title"], value=f"[Read More](https://{baseURL}{itemLink})", inline=True)
                else:
                    em = discord.Embed(title="Error",
                                       description=f"Could not find English results for \"{searchterm.title()}\" in {'' if not stable else 'stable '}wiki.",
                                       colour=discord.Colour.red())
                    await bufferMsg.edit(embed=em) if ctx.prefix is not None else await bufferMsg.delete()
                await bufferMsg.edit(embed=em)
            else:
                await bufferMsg.edit(embed=await wiki_embed(url))


class FactorioCog(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        type(self).__name__ = "Factorio Commands"

    @commands.command(aliases=["mod"])
    async def linkmod(self, ctx, *, modname=None):
        """
        Search for a mod in [the Factorio mod portal](https://mods.factorio.com).
        """
        if not modname:
            em = discord.Embed(title="Error",
                               description="To use the command, you need to enter a mod name to search.",
                               colour=discord.Colour.red())
            await ctx.send(embed=em)
        else:
            em = discord.Embed(title=f"Searching for \"{modname.title()}\" in mods.factorio.com...",
                               description="This may take a bit.",
                               colour=discord.Colour.gold())
            bufferMsg = await ctx.send(embed=em)
            async with ctx.channel.typing():
                response = await get_soup(f"https://mods.factorio.com/query/{modname.title()}")
                if response[0] == 200:
                    soup = response[1]
                    if " 0 " in soup.find("span", class_="active-filters-bar-total-mods").string:
                        em = discord.Embed(title="Error",
                                           description=f"Could not find \"{modname.title()}\" in mod portal.",
                                           colour=discord.Colour.red())
                        await bufferMsg.edit(embed=em) if ctx.prefix is not None else await bufferMsg.delete()

                    elif soup.find_all("div", class_="mod-card"):
                        if len(soup.find_all("div", class_="mod-card")) > 1:
                            em = discord.Embed(title=f"Search results for \"{modname}\"",
                                               colour=discord.Colour.gold())
                            i = 0
                            for result in soup.find_all("div", class_="mod-card"):
                                if i <= 4:
                                    title = result.find("h2", class_="mod-card-title").find("a")
                                    if title.string.title() == modname.title():
                                        em = mod_embed(result)
                                        break
                                    author = result.find("div", class_="mod-card-author").find("a").string
                                    summary = markdownEx.sub(r"\\\1", result.find('div', class_='mod-card-summary').string)
                                    em.add_field(name=f"{title.string} (by {author})",
                                                 value=f"{summary} [*Read More*](https://mods.factorio.com/mods{title['href']})")
                                    i += 1

                        else:
                            em = mod_embed(soup.find("div", class_="mod-card"))

                        await bufferMsg.edit(embed=em)
                else:
                    em = discord.Embed(title="Error",
                                       description="Couldn't reach mods.factorio.com.",
                                       colour=discord.Colour.red())
                    await bufferMsg.edit(embed=em) if ctx.prefix is not None else await bufferMsg.delete()

    @commands.command()
    async def wiki(self, ctx, *, searchterm=None):
        """
        Searches for a term in the [official Factorio wiki](https://wiki.factorio.com/).
        """
        await process_wiki(ctx, searchterm)

    @commands.command()
    async def stablewiki(self, ctx, *, searchterm=None):
        """
        Searches for a term in the [official Stable Factorio wiki](https://stable.wiki.factorio.com/).
        """
        await process_wiki(ctx, searchterm, stable=True)

    @commands.command()
    async def fff(self, ctx, number=None):
        """
        Links an fff with the number provided.
        """
        bufferMsg = None
        if number is not None:
            try:
                number = int(number)
                em = await embed_fff(number)
            except ValueError:
                em = discord.Embed(title="Error",
                                   description="To use the command, you need to input a number.",
                                   colour=discord.Colour.red())
        else:
            em = discord.Embed(title=f"Searching for latest FFF...",
                               description="This may take a bit.",
                               colour=discord.Colour.gold())
            bufferMsg = await ctx.send(embed=em)
            async with ctx.channel.typing():
                async with aiohttp.ClientSession() as client:
                    async with client.get("https://www.factorio.com/blog/rss") as resp:
                        status = resp.status
                        r = await resp.text()
                if status == 200:
                    rss = feedparser.parse(r)
                    i = 0
                    entry = rss.entries[i]
                    while "friday facts" not in entry.title.lower():
                        i += 1
                        entry = rss.entries[i]
                    em = await embed_fff(fffEx.search(entry.title).group(1))
        if not bufferMsg:
            await ctx.send(embed=em)
        else:
            await bufferMsg.edit(embed=em)

    @commands.command(name="0.17", aliases=[".17"])
    async def dot17(self, ctx):
        """
        Returns info about the release date of 0.17.
        """
        await ctx.invoke(self.bot.get_command("faq"), query="0.17")


def setup(bot):
    bot.add_cog(FactorioCog(bot))
