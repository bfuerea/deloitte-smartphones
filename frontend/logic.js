// logic.js

// Hardcoded exchange rate: 1 EUR = 5.27 RON
const EUR_TO_RON_RATE = 5.27; 

function standardizeModelName(rawName) {
    let name = rawName;
    if (!/samsung/i.test(name)) name = "Samsung " + name;
    if (!/galaxy/i.test(name)) name = name.replace(/samsung/i, "Samsung Galaxy");
    
    return name.split(' ').map(word => {
        const up = word.toUpperCase();
        if (up === "FE") return "FE";
        if (up === "PLUS") return "Plus";
        return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    }).join(' ').replace(/\s+/g, ' ').trim();
}

function processPhoneData(deloitteData, emagData, subsidyAmount) {
    return deloitteData.map(dPhone => {
        const cleanName = standardizeModelName(dPhone.Model);
        const ePhone = emagData.find(e => e.Model === cleanName && e.Storage === dPhone.Storage);
        
        // 1. Calculate the total Deloitte price in RON
        const deloittePriceRON = dPhone.Deloitte_Price * EUR_TO_RON_RATE;
        
        // 2. Calculate the subsidy in RON
        const subsidyRON = subsidyAmount * EUR_TO_RON_RATE;
        
        // 3. Out-of-Pocket = Full Price - Subsidy (cannot be less than 0)
        const outOfPocketRON = Math.max(0, deloittePriceRON - subsidyRON);
        
        const emagPriceRON = ePhone ? ePhone.eMAG_Price : null;
        
        let emagPriceEUR = null;
        if (emagPriceRON !== null) {
            emagPriceEUR = parseFloat((emagPriceRON / EUR_TO_RON_RATE).toFixed(2));
        }
        
        const diff = emagPriceRON !== null ? (emagPriceRON - outOfPocketRON) : null;

        return {
            Model: cleanName,
            Storage: dPhone.Storage,
            Deloitte_Price_RON: parseFloat(deloittePriceRON.toFixed(2)),
            Out_of_Pocket_RON: parseFloat(outOfPocketRON.toFixed(2)), // This is what the graph needs
            eMAG_Price_EUR: emagPriceEUR,
            eMAG_Price_RON: emagPriceRON,
            eMAG_Rating: ePhone ? ePhone.eMAG_Rating : null,
            Difference: diff !== null ? parseFloat(diff.toFixed(2)) : null
        };
    });
}
