"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports.setAGETypes = setAGETypes;
exports.AGTypeParse = AGTypeParse;

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _antlr = _interopRequireDefault(require("antlr4"));

var _AgtypeLexer = _interopRequireDefault(require("./AgtypeLexer"));

var _AgtypeParser = _interopRequireDefault(require("./AgtypeParser"));

var _CustomAgTypeListener = _interopRequireDefault(require("./CustomAgTypeListener"));

function AGTypeParse(input) {
  var chars = new _antlr["default"].InputStream(input);
  var lexer = new _AgtypeLexer["default"](chars);
  var tokens = new _antlr["default"].CommonTokenStream(lexer);
  var parser = new _AgtypeParser["default"](tokens);
  parser.buildParseTrees = true;
  var tree = parser.agType();
  var printer = new _CustomAgTypeListener["default"]();

  _antlr["default"].tree.ParseTreeWalker.DEFAULT.walk(printer, tree);

  return printer.getResult();
}

function setAGETypes(_x, _x2) {
  return _setAGETypes.apply(this, arguments);
}

function _setAGETypes() {
  _setAGETypes = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(client, types) {
    var oidResults;
    return _regenerator["default"].wrap(function _callee$(_context) {
      while (1) {
        switch (_context.prev = _context.next) {
          case 0:
            _context.next = 2;
            return client.query("\n        CREATE EXTENSION IF NOT EXISTS age;\n        LOAD 'age';\n        SET search_path = ag_catalog, \"$user\", public;\n    ");

          case 2:
            _context.next = 4;
            return client.query("\n        select typelem\n        from pg_type\n        where typname = '_agtype';");

          case 4:
            oidResults = _context.sent;

            if (!(oidResults.rows.length < 1)) {
              _context.next = 7;
              break;
            }

            throw new Error();

          case 7:
            types.setTypeParser(oidResults.rows[0].typelem, AGTypeParse);

          case 8:
          case "end":
            return _context.stop();
        }
      }
    }, _callee);
  }));
  return _setAGETypes.apply(this, arguments);
}
//# sourceMappingURL=AGEParser.js.map