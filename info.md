The `index.html` file itself does not contain the logic for the `emag.ro` web crawler. Instead, it demonstrates how the *results* of a data aggregation process are displayed. The process relies on two main components: `index.html` (which fetches data and renders the UI) and `logic.js` (which contains the data processing logic).

The workflow inferred from the code is as follows:

1. __Data Acquisition (Implied Crawler):__ An external process (the web crawler for `emag.ro` and likely another source for Deloitte data) gathers data, presumably from websites like `emag.ro`, and compiles this information into a file named `data.json` within the project directory (`d:\Work\deloitte_phones`).

2. __Data Processing (`logic.js`):__ The JavaScript code in `index.html` fetches this `data.json` and calls the `processPhoneData` function defined in `logic.js`.

   - The `processPhoneData` function is responsible for comparing the prices and calculating the subsidy difference.
   - It uses `standardizeModelName` to clean up phone model names (e.g., normalizing "Samsung" names).
   - It attempts to match Deloitte phone data with eMAG data based on the cleaned model name and storage capacity.
   - It calculates the "Out of Pocket" cost by subtracting the subsidy from the Deloitte price.
   - It extracts the `eMAG_Price` and `eMAG_Rating` from the eMAG data.
   - It calculates the final `Difference` between the eMAG price and the calculated out-of-pocket cost.

3. __Presentation (`index.html`):__ The processed data is then used to render a comparison table (`#dataTable`) and a bar chart (`#costChart`) on the web page, displaying the subsidy information and the price comparison between Deloitte and eMAG.

In summary, the provided files show the *result* stage of the process (data processing and visualization) rather than the actual *web crawling* implementation. The actual crawling mechanism is external to these files and is responsible for populating `data.json`.


The provided files (`index.html` and `logic.js`) demonstrate the data *processing and presentation* layer of an application that compares phone subsidies between Deloitte and eMAG, rather than showing the actual implementation of the external `emag.ro` web crawler itself.

The web crawler's role, based on this context, is **external** to these files: it is responsible for fetching raw data from the web (like `emag.ro`) and compiling it into the `data.json` file.

The `logic.js` file contains the crucial functions that interpret and compare this raw data to calculate the final results displayed on the dashboard. Here is a detailed breakdown of how the logic works:

### 1. `standardizeModelName(rawName)` Function
This function is designed to normalize phone model names from various sources into a consistent format. This is essential for accurately matching Deloitte prices with eMAG prices.
*   **Purpose:** To ensure that model names from different data sources (Deloitte and eMAG) referring to the same phone are recognized as identical.
*   **Process:**
    *   It prepends "Samsung" to names that don't contain it.
    *   It replaces "samsung" with "Samsung Galaxy" if it doesn't contain "galaxy".
    *   It then splits the name into words and applies specific cleaning rules:
        *   "FE" is kept as "FE".
        *   "PLUS" is replaced with "Plus".
        *   All other words are title-cased (first letter capitalized, rest lowercase).
    *   Finally, it removes extra whitespace and trims the string, resulting in a consistent model identifier.

### 2. `processPhoneData(deloitteData, emagData, subsidyAmount)` Function
This function performs the core comparison and calculation between the two datasets (`deloitteData` and `emagData`) and the subsidy amount.
*   **Input:** It takes the Deloitte data, the eMAG data, and the subsidy amount as input.
*   **Process:** It iterates through each phone in the `deloitteData` (Deloitte's data) and performs the following steps:
    1.  **Standardize Model:** It first calls `standardizeModelName()` on the Deloitte model name to get a `cleanName`.
    2.  **Find eMAG Match:** It searches the `emagData` array to find a matching record based on both the `cleanName` and the `Storage` capacity. This implies that the eMAG data must be structured similarly to the Deloitte data for a successful match.
    3.  **Calculate Out-of-Pocket Cost:** It calculates the "Out of Pocket" cost by subtracting the `subsidyAmount` from the `Deloitte_Price`, ensuring the result is not negative using `Math.max(0, ...)`.
    4.  **Retrieve eMAG Price and Rating:** If a match is found in `emagData`, it retrieves the `eMAG_Price` and `eMAG_Rating`.
    5.  **Calculate Difference:** It calculates the `Difference` by subtracting the calculated "Out of Pocket" cost from the retrieved `eMAG_Price`. This difference represents the potential savings or additional cost when buying via eMAG versus the subsidized Deloitte price.
    6.  **Return Result:** It compiles all the relevant information (Model, Storage, Prices, Rating, and Difference) into a new object for each phone.

**Conclusion on the Crawler:**

The web crawler's primary job is to populate `data.json` with structured pricing information from sources like `emag.ro`. The JavaScript code in `logic.js` then acts as the **analyst**, taking this raw, potentially messy data and performing sophisticated comparison logic to calculate the financial benefit of using the subsidy across different retailers.