#include <iostream>
#include <unordered_map>
#include <vector>
#include <optional>
#include <iomanip>
#include <algorithm>
using namespace std;

// ---------------- ENUM + TOKEN ----------------
enum class Category {
    KW,
    OP,
    ID,
    NUM
};

struct Lexeme {
    Category category;
    string text;
};

// ---------------- SYMBOL RECORD ----------------
struct Record {
    string name;
    vector<int> occurrences;
    optional<string> type;
    optional<double> value;

    bool hasLine(int line) const {
        return find(occurrences.begin(), occurrences.end(), line) != occurrences.end();
    }

    void addLine(int line) {
        if (!hasLine(line)) {
            occurrences.push_back(line);
        }
    }
};

// ---------------- SYMBOL TABLE ----------------
class Table {
    unordered_map<string, Record> kwTable;
    unordered_map<string, Record> opTable;
    unordered_map<string, Record> idTable;
    unordered_map<string, Record> numTable;

    unordered_map<string, Record>& selectTable(Category c) {
        switch (c) {
            case Category::KW: return kwTable;
            case Category::OP: return opTable;
            case Category::ID: return idTable;
            default: return numTable;
        }
    }

public:
    void insert(const Lexeme& lex, int line,
                optional<string> dtype = nullopt,
                optional<double> val = nullopt) {

        auto& table = selectTable(lex.category);

        if (table.find(lex.text) == table.end()) {
            table[lex.text] = {lex.text, {line}, dtype, val};
        } else {
            table[lex.text].addLine(line);
        }
    }

    void printSection(const string& title,
                      const unordered_map<string, Record>& table,
                      bool showType = false,
                      bool showValue = false) const {

        cout << "\n" << title << ":\n";

        for (const auto& [key, rec] : table) {
            cout << " " << left << setw(10) << rec.name;

            if (showType) {
                cout << " (type: " << rec.type.value_or("unknown") << ", ";
            } else if (showValue) {
                cout << " (value: " << rec.value.value_or(0) << ", ";
            } else {
                cout << " (";
            }

            cout << "lines: ";
            for (int l : rec.occurrences) {
                cout << l << " ";
            }
            cout << "\b)\n";
        }
    }

    void display() const {
        printSection("Keywords", kwTable);
        printSection("Operators", opTable);
        printSection("Identifiers", idTable, true);
        printSection("Numeric Constants", numTable, false, true);
    }
};

// ---------------- CHECK FUNCTIONS ----------------
bool checkKeyword(const string& s) {
    static vector<string> list = {
        "int","float","double","char","bool","if","else","while","for",
        "return","true","false","break","continue","switch","case","default"
    };
    return find(list.begin(), list.end(), s) != list.end();
}

bool checkOperator(const string& s) {
    static vector<string> ops = {
        "+","-","*","/","=","==","!=","<",">","<=",">=","&&","||","!"
    };
    return find(ops.begin(), ops.end(), s) != ops.end();
}

bool checkIdentifier(const string& s) {
    if (!isalpha(s[0]) && s[0] != '_') return false;

    for (char c : s) {
        if (!isalnum(c) && c != '_') return false;
    }
    return true;
}

bool checkNumber(const string& s) {
    try {
        stod(s);
        return true;
    } catch (...) {
        return false;
    }
}

bool isSeparator(char c) {
    string sep = " \n\t;(){}:";
    return sep.find(c) != string::npos;
}

// ---------------- LEXER ----------------
void scan(const string& code, Table& table) {
    int line = 1;
    string current;
    string activeType;

    for (size_t i = 0; i <= code.size(); i++) {

        if (i == code.size() || isSeparator(code[i])) {

            if (!current.empty()) {

                if (checkOperator(current)) {
                    table.insert({Category::OP, current}, line);
                }
                else if (checkKeyword(current)) {
                    table.insert({Category::KW, current}, line);

                    if (current == "int" || current == "float" ||
                        current == "double" || current == "char" || current == "bool") {
                        activeType = current;
                    } else {
                        activeType.clear();
                    }
                }
                else if (checkIdentifier(current)) {
                    table.insert({Category::ID, current}, line,
                                 activeType.empty() ? "unknown" : activeType);
                }
                else if (checkNumber(current)) {
                    table.insert({Category::NUM, current}, line,
                                 nullopt, stod(current));
                }

                current.clear();
            }

            if (i < code.size() && code[i] == '\n') {
                line++;
            }
        }
        else {
            current += code[i];
        }
    }
}

// ---------------- MAIN ----------------
int main() {
    Table table;

    string code =
        "int a = 10;\n"
        "float b = 20.5;\n"
        "double c = a + b;\n"
        "bool flag = true;\n"
        "if (a <= b && flag) {\n"
        " c = c + 1;\n"
        "}\n";

    scan(code, table);
    table.display();

    return 0;
}