**Test**  
Media Quotes Collection System

***1\. Task***

Develop an automated system for detecting, collecting and presenting media quotes from the two sources specified below, where such media quotes are relevant to developing substantial understanding of the *current trends and expectations about Russia's war on Ukraine*.
Sources:
[https://www.reuters.com](https://www.reuters.com)
[https://www.bbc.co.uk/](https://www.bbc.co.uk/)
*Adding other authoritative sources to the scope is a plus (Bloomberg, FT, The Economist etc.)*

***2\. Functional Requirements***

*2.1. Database creation*  
• A database for storing relevant quotes should be stood up, with the following metadata:  
Quote ID  
Quote text  
Source ID  
Source name  
Publication date  
Article name  
Article weblink
Extraction date  
\[other if necessary\]

• A storage of acquired data and the chosen stack should be optimized for further efficient use of these data in LLM contextualization (for instance, storing data in json format, using Obsidian, Neo4j or any better auditable solutions). 

*2.2. Relevant articles and quotes detection*  
• The system must automatically and deterministically (not probabilistically) check each required source each day, and detect relevant articles.  
• The system should ‘read’ the relevant selected articles, then reason about whether there are relevant quotes (quotes that allow developing substantial understanding of the current trends and expectations about Russia's war on Ukraine).

*2.3. Quotes collection*  
• For each detected relevant quote:  
• Extract and save quotes and their metadata from the article’s webpage.  
• The extracted quote and metadata should be verified using deterministic tools (no ‘frankenquotes’, no incorrect/hallucinated sources, no empty metadata etc.)  
• Maintain the original formatting structure of the content.

• Relevant quotes for the period from October 1, 2026 until the current date should be collected and stored in the database.

• It would be a plus if the user can select the specific period from the past for which he wants to detect and collect relevant quotes to append the database (for instance, for August-September 2026), and the system will do that on request. 

• It would be an additional plus if the user can name another media (or ‘think-tank’) source, and the system automatically builds a pipeline for this new source: detection and collection of relevant quotes from this new source, appending the database with the quotes from this new source for the period from October 1, 2026.

*2.4. Reasoning about non-obsolete quotes and presenting them*  
• Each day the system should deliver an MD file where only non-obsolete material quotes (all recent, not outdated) for the rolling past 14-day period will be presented:  
• There should be a title of the file, where the user will see a period for which the quotes are presented.  
• The quotes should be grouped by separate sections, for instance: War Intensity, International Support, Peace Initiatives, Sanctions, Conflicting Parties’ Resources, etc.  
• The system should reason about what quotes to extract from the database into each of the sections, and then do that. *Obsolete quotes (with outdated facts that are superseded by known new facts) should not be shown.*
• The quotes should be extracted into relevant sections deterministically (no change in the text of the quotes nor into their metadata), with the text of the quote, source name, publication name, article name and hyperlink to the original page.   
• Under the name of each section there should be a brief non-deterministic inference from LLM, accurately describing in 1-2 sentences the meaning resulting from the selected quotes. For instance: “*Commentators highlight facts indicating that the war in Ukraine is intensifying*"

***3\. Output Requirements***  
• Auditable database with quotes and relevant metadata.  
• Dashboard for the user where he can access latest MD files, see logs, access the database etc.  
• GitHub with relevant repository.

***4\. Quality Requirements***  
• All material articles from mandatory sources should be detected.  
• All material quotes from those articles should be detected.  
• The quotes should be relevant, substantial for the selected domain, self-explanatory, cleansed (e.g., no contamination with text from ads/page numbers/table symbols etc).  
• All material quotes are extracted and saved (i.e. no important articles and quotes were left out).  
• Accurate extraction of quotes and metadata from the primary source is made.  
• Accurate load for the MD file (verbatim and categorisation into specific sections) is made. Obsolete quotes (with outdated facts that are superseded by known new facts) should not be shown.   
• Relevant interpretation of quotes is made for the description of their meaning in the MD file.  
• The database of quotes is auditable, easily integratable with frontier labs’ LLMs.   
• Logs are saved for auditability/traceability.  
• Processing should handle various potential errors (network issues, unavailable content, etc.)  
• The system should be able to process media articles regardless of their size or content type.
