/**
 * OmniFlow JavaScript Plugin
 * This plugin multiplies a number by 2 and returns the result
 *
 * Input example:
 * {
 *   "number": 5
 * }
 *
 * Output example:
 * {
 *   "message": "JS plugin executed successfully!",
 *   "result": 10
 * }
 */

module.exports = async (event) => {
    try {
        const inputNumber = event.number;

        let result;
        if (typeof inputNumber === "number") {
            result = inputNumber * 2;
        } else {
            result = null;
        }

        return {
            message: "JS plugin executed successfully!",
            result: result
        };
    } catch (err) {
        return {
            message: "JS plugin error",
            error: err.toString()
        };
    }
};
