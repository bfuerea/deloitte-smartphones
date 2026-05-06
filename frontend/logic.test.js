const { standardizeModelName, processPhoneData, EXCHANGE_RATE_RON_TO_EUR } = require('./logic');

describe('Data Standardization', () => {
    test('standardizes missing Samsung and Galaxy tags', () => {
        expect(standardizeModelName('A26')).toBe('Samsung Galaxy A26');
        expect(standardizeModelName('Samsung A37')).toBe('Samsung Galaxy A37');
    });

    test('handles specific capitalization constraints (FE, Plus)', () => {
        expect(standardizeModelName('S26 PLUS')).toBe('Samsung Galaxy S26 Plus');
        expect(standardizeModelName('Samsung S25 fe')).toBe('Samsung Galaxy S25 FE');
    });
});

describe('Data Processing', () => {
    const mockDeloitte = [
        { "Model": "Samsung A26", "Storage": "128GB", "Deloitte_Price": 220.14, "Currency": "EUR" },
        { "Model": "S26 PLUS", "Storage": "256GB", "Deloitte_Price": 1059.09, "Currency": "EUR" }
    ];
    const mockEmag = [
        { "Model": "Samsung Galaxy A26", "Storage": "128GB", "eMAG_Price": 1063.99, "eMAG_Rating": 4.2, "Currency": "RON" },
        { "Model": "Samsung Galaxy S26 Plus", "Storage": "256GB", "eMAG_Price": 1020.00, "eMAG_Rating": 4.7, "Currency": "RON" }
    ];
    const mockSubsidy = 271.00;

    test('clips out of pocket cost to zero if under subsidy', () => {
        const result = processPhoneData(mockDeloitte, mockEmag, mockSubsidy);
        const a26 = result.find(r => r.Model === 'Samsung Galaxy A26');
        expect(a26.Out_of_Pocket).toBe(0);
    });

    test('calculates correct out of pocket for expensive phones', () => {
        const result = processPhoneData(mockDeloitte, mockEmag, mockSubsidy);
        const s26 = result.find(r => r.Model === 'Samsung Galaxy S26 Plus');
        expect(s26.Out_of_Pocket).toBe(788.09); // 1059.09 - 271
    });

    test('converts eMAG price from RON to EUR', () => {
        const result = processPhoneData(mockDeloitte, mockEmag, mockSubsidy);
        const a26 = result.find(r => r.Model === 'Samsung Galaxy A26');
        const expectedEUR = parseFloat((1063.99 / EXCHANGE_RATE_RON_TO_EUR).toFixed(2));
        expect(a26.eMAG_Price).toBe(expectedEUR);
        expect(a26.eMAG_Price_RON).toBe(1063.99);
    });

    test('includes eMAG_Price_RON field with original RON value', () => {
        const result = processPhoneData(mockDeloitte, mockEmag, mockSubsidy);
        const s26 = result.find(r => r.Model === 'Samsung Galaxy S26 Plus');
        expect(s26.eMAG_Price_RON).toBe(1020.00);
    });
});
