# Glossary

Plain definitions of the words the Resource Explorer tutorial uses. Each part links a term here the first time it
appears. Where the screen uses a different word, the definition says so.

| Term | Definition |
|---|---|
| <a id="resource-explorer"></a>**Resource Explorer** | A web tool that looks at data sources, measures them, records what people know about them, and publishes what it learns to Egeria. |
| <a id="egeria"></a>**Egeria** | An open-source metadata catalog: the shared record of what data an organization has. Resource Explorer reads from it and writes to it. |
| <a id="egeria-quickstart"></a>**Egeria Quickstart** | A demonstration environment of Egeria, set up around a fictitious company, Coco Pharmaceuticals, with made-up users. Nothing in it is permanent. |
| <a id="coco-pharmaceuticals"></a>**Coco Pharmaceuticals** | The fictitious company whose data and people fill the Egeria Quickstart. |
| <a id="egeria-explorer"></a>**Egeria Explorer** | A tool in the Quickstart portal for browsing what Egeria holds, including what you published and Resource Explorer's questions and perspectives. |
| <a id="catalog"></a>**Catalog** | The shared record of data sources and what is known about them. Here, the catalog is Egeria. |
| <a id="data-source-resource"></a>**Data source (resource)** | Something that holds data: a database, a file system or a code repository. The screen says "resource"; the tutorial says "data source" and notes once that they are the same. |
| <a id="repository"></a>**Repository** | A code repository on GitHub. Resource Explorer reads its files and history. |
| <a id="database"></a>**Database** | A PostgreSQL database. Resource Explorer reads its catalog first, and its rows only when you allow it. |
| <a id="file-system"></a>**File system** | A folder, and everything under it, that this server or Egeria can read. |
| <a id="register"></a>**Register** | Tell Resource Explorer about a data source so it can look at it. Registering writes nothing to Egeria. |
| <a id="slug"></a>**Slug** | The short name with no spaces that Resource Explorer gives a data source, used in addresses. You choose it when you register. |
| <a id="stage"></a>**Stage** | One of the six steps a data source goes through: Scouting, Discovery, Assessment, Analysis, Enrichment, Curate. |
| <a id="the-funnel"></a>**The funnel** | The idea behind the stages: cheap, basic questions first, costlier ones only for the data sources worth them. |
| <a id="question"></a>**Question** | One thing Resource Explorer asks about a data source, such as "Is this repository actively maintained?". Each stage has its own. |
| <a id="answer"></a>**Answer** | What Resource Explorer found for a question, with the source it came from. |
| <a id="analysis"></a>**Analysis** | One check that measures something, such as counting tables or finding files no one has opened. |
| <a id="survey"></a>**Survey** | A set of analyses run together on one data source. |
| <a id="overview-survey"></a>**Overview survey** | The survey that runs all of a stage's analyses for one kind of data source. The good first run on a stage. |
| <a id="survey-report"></a>**Survey report** | The results of one survey, as Egeria stores them. |
| <a id="measure"></a>**Measure** | A number an analysis produced, with its unit and the time it was taken. |
| <a id="annotation"></a>**Annotation** | One measurement or observation attached to a data source in Egeria. A survey report holds many. |
| <a id="as-of"></a>**"as of"** | The time a number was read. Numbers from a source that changes carry one. |
| <a id="nothing-found-not-run"></a>**Nothing found / not run** | "Nothing found" means it was measured and there was nothing. "Not run" means nobody has looked. They are never the same. |
| <a id="enrichment"></a>**Enrichment** | What a person adds that no check can measure: owner, sensitivity, intended use. |
| <a id="request-for-action-rfa"></a>**Request for action (RFA)** | Something a check could not settle, so a person must. The **RFAs** drawer lists them. |
| <a id="curate"></a>**Curate** | Decide what Resource Explorer will send to Egeria. Every choice is saved in Resource Explorer only. |
| <a id="include-leave-out"></a>**Include / Leave out** | The choice, per schema, table, file or folder, of whether it is published. |
| <a id="tag"></a>**Tag** | A public label in Egeria, such as "sales". Everyone's tag of the same name is one tag. |
| <a id="group"></a>**Group** | A set of data sources that belong together, such as one product's. In Egeria a group becomes a collection. |
| <a id="declared-measures"></a>**Declared measures** | The screen's heading on Curate's "Publish · declared measures" table: the measurements the survey offers to publish. You choose, row by row, which ones go to Egeria. The tutorial says "the measures the survey offers to publish" and explains the screen word once. |
| <a id="rating-and-note"></a>**Rating and note** | Your judgment of a data source. Published under your user id; one rating per person. |
| <a id="publish"></a>**Publish** | Send your choices to Egeria. Only a person's press publishes, and only that person's own work. |
| <a id="confirmed-read-back"></a>**Confirmed (read back)** | Resource Explorer read the published item back from Egeria and found it there. |
| <a id="work-list"></a>**Work list** | A set of data sources of one kind, laid out side by side against one stage's questions, to compare and narrow them. |
| <a id="investigation"></a>**Investigation** | A record of why you are looking at a set of data sources: a name, a description and its purposes. |
| <a id="purpose"></a>**Purpose** | What an investigation is for, such as Explore or Learn. Purposes put the most relevant questions first. |
| <a id="scope"></a>**Scope** | The data sources an investigation covers. It can mix databases, file systems and repositories. |
| <a id="current-investigation"></a>**Current investigation** | The one you are working in now. Questions are ordered by its purposes, and data sources can be added to its scope in one click. |
| <a id="journal"></a>**Journal** | An investigation's running notes, such as chat answers you chose to keep. |
| <a id="disposition"></a>**Disposition** | Your verdict on a data source within an investigation, such as tracking, using or abandoned. |
| <a id="perspective"></a>**Perspective** | Who is asking, such as Data Owner or Steward. Choosing one narrows the questions. |
| <a id="activity"></a>**Activity** | The log of everything that has run or been published. |
| <a id="credential"></a>**Credential** | The user name and password Resource Explorer uses to read a database. Stored encrypted. |
| <a id="mount-local-path"></a>**Mount (local path)** | Where the folder of a file system is found on this server. |
