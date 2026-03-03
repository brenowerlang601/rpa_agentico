import asyncio

from pydantic import BaseModel, Field

from browser_use import Agent, Browser, ChatOpenAI


async def main():
	
	browser = Browser(cdp_url='http://localhost:9222')

	llm = ChatOpenAI(model='gpt-5.1')

	# Task prompt
	task = f"""
    Acesse o ERP da systextil e faça login com usuario: INTERSYS , senha: A%h^9l89
    Site:
    - LINK ERP: https://devs.systextil.com.br/systextil/
    """

	# Create agent with structured output
	agent = Agent(
		browser=browser,
		llm=llm,
		task=task,
	)

	# Run the agent
	result = await agent.run()
	return result


if __name__ == '__main__':


	asyncio.run(main())

