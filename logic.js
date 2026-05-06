function standardizeModelName(rawName) {
    let name = rawName;
    if (!/samsung/i.test(name)) name = "Samsung " + name;
    if (!/galaxy/i.test(name)) name = name.replace(/samsung/i, "Samsung Galaxy");
    
    return name.split(' ').map(word => {
        if (word.toUpperCase() === "FE") return "FE";
        if (word.toUpperCase() === "PLUS") return "Plus";
        return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    }).join(' ').replace(/\s+/g, ' ').trim();
}

function processPhoneData(deloitteData, emagData, subsidyAmount) {
    return deloitteData.map(dPhone => {
        const cleanName = standardizeModelName(dPhone.Model);
        
        const ePhone = emagData.find(e => e.Model === cleanName && e.Storage === dPhone.Storage);
        const outOfPocket = Math.max(0, dPhone.Deloitte_Price - subsidyAmount);
        const emagPrice = ePhone ? ePhone.eMAG_Price : null;
        const diff = emagPrice !== null ? (emagPrice - outOfPocket) : null;

        return {
            Model: cleanName,
            Storage: dPhone.Storage,
            Deloitte_Price: dPhone.Deloitte_Price,
            Out_of_Pocket: parseFloat(outOfPocket.toFixed(2)),
            eMAG_Price: emagPrice,
            eMAG_Rating: ePhone ? ePhone.eMAG_Rating : null,
            Difference: diff !== null ? parseFloat(diff.toFixed(2)) : null
        };
    });
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { standardizeModelName, processPhoneData };
}
